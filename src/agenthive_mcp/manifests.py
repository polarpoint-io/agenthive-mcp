"""The hook manifests, and where each harness expects to find them.

These live in code rather than as files shipped alongside the package
for one reason: ``pip install agenthive-mcp`` gives you an importable
package, not a checkout. Telling somebody to ``cp plugin/claude/...``
after a pip install names a path that does not exist on their machine.

``plugin/<harness>/`` in the repo is generated from this module and
checked by ``tests/test_manifests.py``, so the files a reader browses on
GitHub and the bytes ``agenthive install`` writes cannot drift apart.

Claude Code and VS Code share a hook format but not a manifest shape:
Claude nests under ``hooks[event][].hooks[]``, VS Code is one level
flatter and names session end ``Stop``. Cursor has its own event names
entirely, translated in ``hooks.py``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

HOOK_COMMAND = "agenthive-hook"

_CLAUDE: dict[str, Any] = {
    "hooks": {
        event: [{"hooks": [{"type": "command", "command": HOOK_COMMAND}]}]
        for event in ("SessionStart", "PostToolUse", "SessionEnd")
    }
}

_VSCODE: dict[str, Any] = {
    "version": 1,
    "hooks": {
        event: [{"type": "command", "command": HOOK_COMMAND}]
        for event in ("SessionStart", "PostToolUse", "Stop")
    },
}

_CURSOR: dict[str, Any] = {
    "version": 1,
    "hooks": {
        event: [{"command": HOOK_COMMAND}]
        for event in (
            "beforeSubmitPrompt",
            "afterFileEdit",
            "afterShellCommand",
            "stop",
        )
    },
}

MANIFESTS: dict[str, dict[str, Any]] = {
    "claude": _CLAUDE,
    "vscode": _VSCODE,
    "cursor": _CURSOR,
}

#: Where the repo keeps the browsable copy of each manifest.
REPO_PATHS: dict[str, str] = {
    "claude": "plugin/claude/settings.json",
    "vscode": "plugin/vscode/agenthive.json",
    "cursor": "plugin/cursor/hooks.json",
}


def destination(harness: str, project_dir: str | None = None) -> Path:
    """Absolute path this harness reads its hooks from.

    VS Code's is per-project, so it resolves against ``project_dir``
    (default: the current directory). The other two are per-user.
    """
    home = Path(os.path.expanduser("~"))
    if harness == "claude":
        return home / ".claude" / "settings.json"
    if harness == "cursor":
        return home / ".cursor" / "hooks.json"
    if harness == "vscode":
        root = Path(project_dir) if project_dir else Path.cwd()
        return (root / ".github" / "hooks" / "agenthive.json").resolve()
    raise ValueError(f"unknown harness {harness!r}; expected one of {sorted(MANIFESTS)}")


def render(harness: str) -> str:
    """The manifest as it should appear on disk, newline-terminated."""
    return json.dumps(MANIFESTS[harness], indent=2) + "\n"


def merge(existing: Any, harness: str) -> tuple[dict[str, Any], list[str]]:
    """Fold our hooks into a config that may already have some.

    ``~/.claude/settings.json`` is not ours - it holds the user's own
    settings and quite possibly other people's hooks. Overwriting it to
    install a memory plugin would be an unreasonable thing to do, so
    this adds our entries to the events we use and leaves everything
    else, including other hooks on those same events, untouched.

    Returns the merged config and the events actually changed, so a
    no-op install can say so rather than claiming it did something.
    """
    ours = MANIFESTS[harness]
    if not isinstance(existing, dict):
        return json.loads(json.dumps(ours)), sorted(ours["hooks"])

    merged = json.loads(json.dumps(existing))
    for key, value in ours.items():
        if key != "hooks":
            merged.setdefault(key, value)

    hooks = merged.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
    merged["hooks"] = hooks

    changed: list[str] = []
    for event, entries in ours["hooks"].items():
        current = hooks.get(event)
        if not isinstance(current, list):
            # Either absent, or a shape we do not understand. Replacing a
            # value we cannot parse would silently drop whatever it was,
            # so only write when there is nothing there at all.
            if current is None:
                hooks[event] = json.loads(json.dumps(entries))
                changed.append(event)
            continue
        if _already_installed(current):
            continue
        current.extend(json.loads(json.dumps(entries)))
        changed.append(event)
    return merged, changed


def _already_installed(entries: list[Any]) -> bool:
    """True when one of these entries already runs our hook command."""
    return HOOK_COMMAND in json.dumps(entries)


def uninstall(existing: Any, harness: str) -> tuple[dict[str, Any], list[str]]:
    """Remove only the entries that run our hook command.

    The mirror of ``merge``: it takes back what we put in and leaves
    anything else on those events alone.
    """
    if not isinstance(existing, dict):
        return {}, []
    merged = json.loads(json.dumps(existing))
    hooks = merged.get("hooks")
    if not isinstance(hooks, dict):
        return merged, []

    changed: list[str] = []
    for event in list(hooks):
        entries = hooks[event]
        if not isinstance(entries, list):
            continue
        kept = [e for e in entries if HOOK_COMMAND not in json.dumps(e)]
        if len(kept) != len(entries):
            changed.append(event)
            if kept:
                hooks[event] = kept
            else:
                del hooks[event]
    return merged, changed
