"""
Tools the team loads and never uses.

Every loaded tool's definition is sent with every request, so an unused
one is a standing tax on every turn of every session. This watches what
a session actually calls, pools that with the rest of the team, and
writes a deny list once the evidence is there.

Two things keep it safe. Evidence is pooled across PEOPLE, so one
engineer who never touches a tool cannot remove it for everyone. And
every removal is reversible with one command - being wrong costs a
command, not an answer.
"""

from __future__ import annotations

import json
import pathlib

from .client import AgentHiveConfigError, AgentHiveError, get_config, request

# Where each harness keeps its own tool permissions. Written through the
# agent's OWN settings rather than by intercepting anything, so nothing
# depends on the agent learning a new mechanism.
TARGETS = {
    "claude": (".claude/settings.local.json", "claude"),
    "vscode": (".vscode/settings.json", "vscode"),
    "cursor": (".cursor/settings.json", "cursor"),
}


def report(loaded, used, session_id: str = "") -> None:
    """Send this session's evidence to the team pool. Never raises."""
    if not loaded:
        return
    try:
        _, _, team_id = get_config()
        request("POST", f"/teams/{team_id}/tool-usage",
                body={"loaded": sorted(set(loaded)), "used": sorted(set(used)),
                      "session_id": session_id})
    except (AgentHiveConfigError, AgentHiveError):
        pass


def proposal() -> dict:
    try:
        _, _, team_id = get_config()
        return request("GET", f"/teams/{team_id}/tool-pruning")
    except (AgentHiveConfigError, AgentHiveError):
        return {"prune": [], "keep": [], "not_yet": []}


def _load(path: pathlib.Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def apply(host: str, cwd: str, tools) -> tuple:
    """Write the deny list into the harness's own settings.

    Returns (written, reason). Refuses rather than guesses when the file
    is not ours to touch: an unparseable settings file is somebody's
    work in progress, and a deny list is never worth corrupting it.

    Only entries this wrote are ever removed later - rules a human
    added are left alone, because losing somebody's own permission rule
    to a cost optimisation would be unforgivable."""
    if not tools:
        return False, "nothing to prune"
    target = TARGETS.get(host)
    if not target:
        return False, f"no settings target known for {host}"

    path = pathlib.Path(cwd) / target[0]
    if path.exists():
        existing = _load(path)
        if not existing and path.stat().st_size > 0:
            return False, f"{target[0]} is not valid JSON; leaving it alone"
    else:
        existing = {}

    perms = existing.setdefault("permissions", {})
    deny = perms.setdefault("deny", [])
    marker = existing.setdefault("agenthive", {})
    ours = set(marker.get("pruned") or [])

    added = []
    for tool in tools:
        rule = f"{tool}"
        if rule not in deny:
            deny.append(rule)
            added.append(rule)
        ours.add(rule)
    marker["pruned"] = sorted(ours)

    if not added:
        return False, "already applied"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(existing, indent=2) + "\n")
    except OSError as e:
        return False, f"could not write {target[0]}: {e}"
    return True, f"pruned {len(added)} tool(s) via {target[0]}"


def restore(host: str, cwd: str) -> tuple:
    """Give back everything this wrote, and nothing else."""
    target = TARGETS.get(host)
    if not target:
        return False, f"no settings target known for {host}"
    path = pathlib.Path(cwd) / target[0]
    if not path.exists():
        return False, "nothing to restore"

    data = _load(path)
    ours = set((data.get("agenthive") or {}).get("pruned") or [])
    if not ours:
        return False, "nothing here was pruned by agenthive"

    deny = (data.get("permissions") or {}).get("deny") or []
    data.setdefault("permissions", {})["deny"] = [d for d in deny if d not in ours]
    data["agenthive"] = {"pruned": [], "restored": True}
    try:
        path.write_text(json.dumps(data, indent=2) + "\n")
    except OSError as e:
        return False, f"could not write {target[0]}: {e}"
    return True, f"restored {len(ours)} tool(s)"
