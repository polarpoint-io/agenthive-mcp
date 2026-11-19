"""Turn a repo of markdown docs into candidate AgentHive memory nodes.

This is deliberately NOT an MCP tool. Every other write path in this repo
(``log_session``) is one agent reporting what *it* just learned, in its
own session. Bulk-importing someone else's existing docs is a different
shape of action - a human decides to backfill a team's memory from its
runbooks once, or after a big docs rewrite - so it lives next to
``install``/``uninstall`` as a terminal command (see cli.py's docstring
for why those are commands and not tools), not as something an agent can
decide to do mid-session.

What this module does NOT do:

- Call AgentHive. ``plan()`` only reads the filesystem and returns the
  nodes that *would* be created; cli.py's ``import-docs`` command is the
  only thing that calls ``client.request`` with them, and only when
  given ``--apply``. Importing a whole repo can produce hundreds of
  pending nodes in one run - that is exactly the queue-flooding failure
  mode AgentHive's review gate exists to be the human-in-the-loop check
  on, so turning that queue flood into two separate steps (see the plan,
  then actually write it) is load-bearing, not a nicety.
- Chunk by length. Chunking is by heading, not by character budget - see
  ``split_sections``.

WHY THIS MODULE HAS ITS OWN DEDUP, ON TOP OF AGENTHIVE'S
----------------------------------------------------------
AgentHive's own ``create_memory`` already rejects an exact title+body
repeat against an *approved* node (``find_approved_duplicate`` in its
db.py). That is not enough on its own: it checks approved nodes only, so
a section still sitting in the pending queue is invisible to it, and
running import-docs again before anyone reviews yesterday's batch would
submit the same section as a second, indistinguishable pending node -
every run, for as long as review lags behind the schedule. That is the
one thing standing between "safe to run by hand occasionally" and "safe
to put on a cron" (see cli.py's --apply for the actual write loop), so
``already_submitted``/``mark_submitted`` below close it: once a given
(team, title, body) has been submitted, re-running without ``--force``
skips it, independent of whether it has been reviewed yet. Change the
body and the fingerprint changes with it, so an edited section is
submitted again on its own.
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .hooklog import state_dir as _state_dir

#: Directories never worth walking into: VCS metadata, dependency trees,
#: and virtualenvs do not hold anything a team would want surfaced as
#: reusable memory, and dependency trees in particular can hold enough
#: markdown (changelogs, per-package READMEs) to turn one real doc into
#: thousands of noise nodes.
_SKIP_DIRS = {
    ".git", "node_modules", "vendor", ".venv", "venv",
    "__pycache__", ".tox", "dist", "build",
}


def _is_excluded(rel_posix: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(rel_posix, pattern) for pattern in patterns)


def discover_markdown_files(root: str, exclude: list[str] | None = None) -> list[Path]:
    """Every ``.md``/``.markdown`` file under ``root``, skipping the dirs
    in ``_SKIP_DIRS`` (at any depth), dotfiles, and anything matching
    ``exclude``. Sorted for a stable, diffable plan across runs.

    ``exclude`` patterns are ``fnmatch``-style globs matched against each
    file's path relative to ``root`` (always posix-style, ``/``, even on
    Windows). ``fnmatch``'s ``*`` matches any run of characters INCLUDING
    ``/`` - unlike shell globbing, ``docs/blog/*`` already excludes
    everything under ``docs/blog`` at any depth, not just its direct
    children; no ``**`` needed.

    ``_SKIP_DIRS`` exists because node_modules/vendor/.venv are never
    plausible team knowledge. ``exclude`` is for the opposite situation:
    a repo (a docs site, most visibly) that mixes genuine operational
    material with a public blog, marketing assets, or anything else a
    team would recognise as not-what-this-is-for only by looking at it.
    """
    root_path = Path(root)
    patterns = exclude or []
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root_path):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            if not name.lower().endswith((".md", ".markdown")):
                continue
            file_path = Path(dirpath) / name
            rel = file_path.relative_to(root_path).as_posix()
            if _is_excluded(rel, patterns):
                continue
            found.append(file_path)
    return sorted(found)


def changed_markdown_files(root: str, since: str, exclude: list[str] | None = None) -> list[Path]:
    """Markdown files added or modified between the git ref ``since`` and
    the working tree's current HEAD - not the whole tree.

    This is the piece that makes a scheduled import SAFE ON A CI RUNNER
    specifically, as opposed to a human's own long-lived machine: the
    local (team, title, body) marker ``already_submitted`` checks lives
    under ``AGENTHIVE_HOOK_STATE_DIR``/``XDG_STATE_HOME``, which an
    ephemeral runner does not keep between invocations unless its cache
    step is wired up for that exact path - get that wrong and every run
    re-walks and resubmits the entire tree. Scoping to ``since`` instead
    means a run never needs to remember anything of its own: the git ref
    (the previous run's commit, say) IS the memory, and it costs nothing
    to keep because the repo's own history already keeps it.

    Deleted files are excluded (``--diff-filter=d``, lowercase - that
    excludes Deleted, not restricts to it) since there is nothing left
    to read; a renamed-with-changes file shows up as its new path.
    """
    root_path = Path(root)
    try:
        done = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=d", f"{since}..HEAD"],
            cwd=root_path, capture_output=True, text=True, timeout=30, check=True,
        )
    except (OSError, subprocess.SubprocessError) as e:
        raise RuntimeError(f"git diff against {since!r} failed under {root}: {e}") from e

    patterns = exclude or []
    found: list[Path] = []
    for line in done.stdout.splitlines():
        rel = line.strip()
        if not rel.lower().endswith((".md", ".markdown")):
            continue
        if _is_excluded(rel, patterns):
            continue
        file_path = root_path / rel
        if file_path.is_file():  # belt-and-braces alongside --diff-filter=d
            found.append(file_path)
    return sorted(found)


@dataclass
class Section:
    title: str
    body: str
    source: str  # path relative to root, for the node's `links`


def split_sections(text: str, heading_prefix: str) -> list[tuple[str, str]]:
    """Split markdown on ATX headings (``#`` / ``##`` / ...) into
    (heading, body) pairs.

    Heading-based, not length-based: AgentHive's retrieval already has
    its own token budgeting (``hub_cutoff``, index-then-fetch) that
    assumes a node is one coherent unit someone would want to fetch on
    its own. A 400-line runbook chunked into one node per character
    budget would split a single procedure across two unrelated nodes;
    chunking on the document's own headings keeps each node the same
    unit the author already wrote.

    Content before the first heading is kept under ``heading_prefix``
    itself (the file's own title) rather than dropped - a short doc
    with no internal headings still becomes exactly one node.
    """
    lines = text.splitlines()
    sections: list[tuple[str, str]] = []
    current_heading = heading_prefix
    current_lines: list[str] = []

    def _flush() -> None:
        body = "\n".join(current_lines).strip()
        if body:
            sections.append((current_heading, body))

    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("#"):
            level_chars = len(stripped) - len(stripped.lstrip("#"))
            rest = stripped[level_chars:].strip()
            if rest:
                _flush()
                current_heading = rest
                current_lines = []
                continue
        current_lines.append(line)
    _flush()
    return sections


def sections_for_file(path: Path, root: Path, min_chars: int) -> list[Section]:
    """The candidate nodes for one file: one per heading-delimited
    section, dropping anything under ``min_chars`` (a one-line "See
    also" or a stray heading with nothing under it is not worth a
    review-queue entry)."""
    rel = path.relative_to(root).as_posix()
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    out = []
    for heading, body in split_sections(text, heading_prefix=rel):
        if len(body) < min_chars:
            continue
        title = rel if heading == rel else f"{rel} — {heading}"
        out.append(Section(title=title, body=body, source=rel))
    return out


def plan(
    root: str,
    min_chars: int = 40,
    exclude: list[str] | None = None,
    since: str | None = None,
) -> list[Section]:
    """Every candidate node this import would create.

    Without ``since``: every markdown file under ``root`` not matching
    ``exclude`` - the whole-tree backfill. With ``since``: only files
    git shows as changed between that ref and HEAD - see
    ``changed_markdown_files`` for why that is the shape a CI run needs.

    Pure filesystem (and, with ``since``, git) read - no network call,
    so a caller can show this to a human before anything is written
    anywhere."""
    root_path = Path(root)
    if since:
        files = changed_markdown_files(root, since, exclude=exclude)
    else:
        files = discover_markdown_files(root, exclude=exclude)
    sections: list[Section] = []
    for file_path in files:
        sections.extend(sections_for_file(file_path, root_path, min_chars))
    return sections


# ---------------------------------------------------------------------------
# Local dedup: has THIS (team, title, body) already been submitted?
#
# Reuses hooklog.py's state_dir() - same AGENTHIVE_HOOK_STATE_DIR /
# XDG_STATE_HOME base the agenthive-log hook already uses - under a
# different filename prefix ("imported-" vs hooklog's "logged-"/"sent-")
# so the two never collide. Nothing here is about session hooks; it just
# saves spinning up a second env var and a second "where does state
# live" decision for the same install.
# ---------------------------------------------------------------------------

def _fingerprint(team_scope: str, section: Section) -> str:
    raw = f"{team_scope}\x00{section.title}\x00{section.body}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]


def _marker_path(team_scope: str, section: Section) -> Path:
    return _state_dir() / ("imported-" + _fingerprint(team_scope, section))


def already_submitted(team_scope: str, section: Section) -> bool:
    """True if this exact (team, title, body) was already written by a
    previous ``--apply``, approved or not. ``team_scope`` is the caller's
    own key (e.g. ``f"{base_url}|{team_id}"``) - this module does not
    know how to reach AgentHive itself, so it takes that as given rather
    than calling ``client.get_config()`` on its own."""
    try:
        return _marker_path(team_scope, section).exists()
    except OSError:
        return False  # state unavailable is "not known to be a repeat"


def mark_submitted(team_scope: str, section: Section) -> None:
    """Record that this (team, title, body) was just submitted."""
    try:
        _marker_path(team_scope, section).write_text("1")
    except OSError:
        pass  # never let bookkeeping break a successful write
