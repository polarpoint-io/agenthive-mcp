"""
Codebase map: what already exists in THIS repo, so "reuse it" costs no
search.

facts.py injects what the TEAM knows (reviewed, server-side, can be
wrong). This is the other half of "don't re-derive what's already
known" - what the repo itself already contains - and it never leaves
the machine and never goes through review, because it isn't a claim
about anything: it's `git ls-files` plus a regex per language, so it
cannot be stale in a way that matters and cannot need correcting.

No model call. Deterministic. Same budget-and-truncate shape as facts:
shared-looking directories (utils, lib, services, components, hooks)
sort first, because a tight budget should drop the dirs an agent is
least likely to need, not whichever happened to come first.
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess

MAX_MAP_CHARS = 2000
MAX_FILES = 4000

_SRC = re.compile(r"\.(py|js|jsx|mjs|cjs|ts|tsx|go|rs|rb|java|kt|php|swift|cs)$")
_SKIP_DIR = re.compile(
    r"(^|/)(node_modules|\.git|dist|build|out|vendor|venv|\.venv|__pycache__|"
    r"coverage|\.next|target|migrations|versions|fixtures|tests?|__tests__|spec)(/|$)")
_SKIP_FILE = re.compile(
    r"(^|/)(test_[^/]*|[^/]*(_test|\.test|\.spec|\.gen|\.min|\.d)\.[a-z]+)$")
_NAME = re.compile(r"^(?:type\s+)?(?:[\w$]+\s+as\s+)?([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)?)$")
_SAFE_PATH = re.compile(r"^[\w.@+$()\[\]/-]+$")
_SHARED = re.compile(r"(util|helper|lib|common|shared|service|component|hook|core|model|schema|api)", re.IGNORECASE)

_LANG = {"py": "py", "js": "js", "jsx": "js", "mjs": "js", "cjs": "js", "ts": "js",
          "tsx": "js", "go": "go", "rs": "rs", "rb": "rb", "java": "jvm", "kt": "jvm",
          "swift": "jvm", "cs": "jvm", "php": "jvm"}

_PATTERNS = {
    "py": [re.compile(r"^(?:async\s+)?def\s+([A-Za-z]\w*)", re.MULTILINE),
           re.compile(r"^class\s+([A-Za-z]\w*)", re.MULTILINE)],
    "js": [re.compile(r"^export\s+(?:default\s+)?(?:async\s+)?"
                       r"(?:function\*?|class|const|let|var|interface|type|enum)\s+"
                       r"([A-Za-z_$][\w$]*)", re.MULTILINE),
           re.compile(r"^export\s*\{([^}]*)\}", re.MULTILINE)],
    "go": [re.compile(r"^func\s+(?:\([^)]*\)\s*)?([A-Z]\w*)", re.MULTILINE),
           re.compile(r"^type\s+([A-Z]\w*)", re.MULTILINE)],
    "rs": [re.compile(r"^pub\s+(?:async\s+)?(?:fn|struct|enum|trait|type)\s+(\w+)", re.MULTILINE)],
    "rb": [re.compile(r"^\s*(?:def|class|module)\s+([A-Za-z][\w.]*)", re.MULTILINE)],
    "jvm": [re.compile(r"^\s*(?:public\s+)?(?:static\s+)?(?:final\s+)?"
                        r"(?:class|interface|enum|record)\s+(\w+)", re.MULTILINE)],
}


def _list_files(root: str) -> list[str]:
    """Tracked files, falling back to a walk for an untracked folder (or
    one with no git at all) - same two-path shape as facts.observe_*,
    never raising on the common failure modes."""
    try:
        out = subprocess.run(
            ["git", "ls-files"], cwd=root, capture_output=True, text=True,
            timeout=3, check=False)
        tracked = [f for f in out.stdout.split("\n") if f][: MAX_FILES * 5]
        if tracked:
            return tracked
    except (OSError, subprocess.SubprocessError):
        pass

    found: list[str] = []

    def walk(d: pathlib.Path, depth: int) -> None:
        if depth > 6 or len(found) > MAX_FILES * 5:
            return
        try:
            entries = list(os.scandir(d))
        except OSError:
            return
        for ent in entries:
            if ent.name.startswith("."):
                continue
            rel = os.path.relpath(ent.path, root).replace(os.sep, "/")
            if ent.is_dir(follow_symlinks=False):
                if not _SKIP_DIR.search(rel + "/"):
                    walk(pathlib.Path(ent.path), depth + 1)
            else:
                found.append(rel)

    walk(pathlib.Path(root), 0)
    return found


def _names_in(file: str, text: str) -> list[str]:
    lang = _LANG.get(file.rsplit(".", 1)[-1])
    names: list[str] = []
    for pattern in _PATTERNS.get(lang, []):
        for m in pattern.finditer(text):
            for part in m.group(1).split(","):
                match = _NAME.match(part.strip())
                if not match:
                    continue
                name = match.group(1)
                if not name.startswith("_") and name not in names:
                    names.append(name)
    return names


def build(root: str = "", budget: int = MAX_MAP_CHARS) -> str:
    """The map as a context block, or "" if the repo yields nothing (an
    empty repo, or one in a language this doesn't parse)."""
    root = root or os.getcwd()
    files = [f for f in _list_files(root)
             if _SAFE_PATH.match(f) and _SRC.search(f)
             and not _SKIP_DIR.search(f) and not _SKIP_FILE.search(f)]
    # Sort BEFORE capping: with more eligible files than MAX_FILES, capping
    # first would truncate the candidate list in whatever order _list_files
    # happened to return it, which can drop shared-looking dirs entirely
    # before they ever get the priority this sort exists to give them.
    files.sort(key=lambda f: (not _SHARED.search(f), f))
    files = files[:MAX_FILES]

    dirs: dict[str, list[list[str]]] = {}
    for f in files:
        path = pathlib.Path(root) / f
        try:
            # is_file() follows symlinks - without this check, a tracked
            # symlink can point anywhere on disk and this default-on,
            # every-session-start hook would read it and inject its
            # contents into model context. Checked before is_file() so a
            # symlink is never opened at all, whatever it points to.
            if path.is_symlink() or not path.is_file():
                continue
            # Bound the READ itself, not just the string after it - a
            # large generated file that slips past the exclusions should
            # cost at most one bounded read, not a full load into memory
            # before the slice ever runs.
            with open(path, encoding="utf-8", errors="ignore") as fh:
                text = fh.read(65536)
        except OSError:
            continue
        names = _names_in(f, text)
        if not names:
            continue
        dirname = str(pathlib.Path(f).parent) if pathlib.Path(f).parent != pathlib.Path(".") else "."
        dirs.setdefault(dirname, []).append(names)

    lines: list[str] = []
    used = 0
    skipped = 0
    for dirname, per_file in dirs.items():
        # First exports say what a file is; in a folder of many files
        # (a UI kit) one name each so the budget covers breadth.
        take = 1 if len(per_file) > 8 else 3
        seen: list[str] = []
        for names in per_file:
            for n in names[:take]:
                if n not in seen:
                    seen.append(n)
        shown = seen[:30]
        line = f"{dirname}/: " + ", ".join(shown) + (", ..." if len(seen) > 30 else "")
        if used + len(line) > budget:
            skipped += 1
            continue
        lines.append(line)
        used += len(line) + 1

    if not lines:
        return ""
    block = ("Codebase map (what already exists; reuse it, read a file only "
              "when you need its details):\n" + "\n".join(lines))
    if skipped:
        block += f"\n({skipped} more folders not listed)"
    return block
