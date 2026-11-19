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
- De-duplicate. AgentHive's own ``create_memory`` already rejects an
  exact title+body repeat against an *approved* node
  (``find_approved_duplicate`` in agenthive's db.py); running this twice
  on an unchanged file does not pile up duplicate pending nodes once the
  first run's nodes are approved. Nothing here needs to re-implement
  that.
- Chunk by length. Chunking is by heading, not by character budget - see
  ``split_sections``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

#: Directories never worth walking into: VCS metadata, dependency trees,
#: and virtualenvs do not hold anything a team would want surfaced as
#: reusable memory, and dependency trees in particular can hold enough
#: markdown (changelogs, per-package READMEs) to turn one real doc into
#: thousands of noise nodes.
_SKIP_DIRS = {
    ".git", "node_modules", "vendor", ".venv", "venv",
    "__pycache__", ".tox", "dist", "build",
}


def discover_markdown_files(root: str) -> list[Path]:
    """Every ``.md``/``.markdown`` file under ``root``, skipping the dirs
    in ``_SKIP_DIRS`` (at any depth) and dotfiles. Sorted for a stable,
    diffable plan across runs."""
    root_path = Path(root)
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root_path):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            if name.lower().endswith((".md", ".markdown")):
                found.append(Path(dirpath) / name)
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


def plan(root: str, min_chars: int = 40) -> list[Section]:
    """Every candidate node this import would create, across every
    markdown file under ``root``. Pure filesystem read - no network
    call, so a caller can show this to a human before anything is
    written anywhere."""
    root_path = Path(root)
    sections: list[Section] = []
    for file_path in discover_markdown_files(root):
        sections.extend(sections_for_file(file_path, root_path, min_chars))
    return sections
