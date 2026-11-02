"""
Environment facts: the part of memory that actually saves tokens.

Recalling a decision is valuable and costs tokens. Recalling that tests
here run with `python -m pytest -q` REPLACES work - the agent would
otherwise have read a handful of files to find out, and that
exploration is where the real spend goes.

So facts are short, injected at session start without an anchor, and
learned from failures that repeat. Nothing here calls a model.
"""

from __future__ import annotations

import json
import pathlib
import re
import time

from .client import AgentHiveConfigError, AgentHiveError, get_config, request

# A fact is injected into every session unconditionally. That is what
# makes it worth its tokens and what makes a wrong one expensive, so the
# budget is small and enforced on both sides.
MAX_FACT_TOKENS = 400

# Commands whose failure then success is worth remembering. A failing
# `ls` teaches nothing; a failing `pytest` followed by a working
# `python -m pytest` is exactly the fact worth keeping.
_WORTH_LEARNING = re.compile(
    r"\b(pytest|tsc|npm|yarn|pnpm|make|cargo|go|gradle|mvn|tox|nox|ruff|mypy)\b")

_NOT_FOUND = re.compile(
    r"(command not found|No such file or directory|is not recognized|"
    r"ModuleNotFoundError|cannot find module)", re.IGNORECASE)


def fetch(cwd: str = "") -> dict:
    """The team's facts, or an empty set if anything is wrong.

    Never raises: this runs at session start, and a session that cannot
    reach AgentHive should begin normally rather than not at all."""
    try:
        _, _, team_id = get_config()
        return request("GET", f"/teams/{team_id}/facts")
    except (AgentHiveConfigError, AgentHiveError):
        return {"facts": [], "approx_tokens": 0}


def render(payload: dict, budget: int = MAX_FACT_TOKENS) -> str:
    """Facts as a block for the agent's context.

    Truncated to a budget rather than sent whole. Facts arrive ordered
    by corroboration, so cutting the tail drops the least-confirmed
    first - and a fact block that grows without limit would reintroduce
    the cost this exists to remove."""
    facts = payload.get("facts") or []
    if not facts:
        return ""

    lines, spent = [], 0
    for fact in facts:
        body = " ".join((fact.get("body") or "").split())
        if not body:
            continue
        cost = len(body) // 4 + 4
        if spent + cost > budget:
            break
        lines.append(f"- {body}")
        spent += cost

    if not lines:
        return ""
    return ("What this team already knows about working here "
            "(from reviewed team memory):\n" + "\n".join(lines))


def _state_path(cwd: str) -> pathlib.Path:
    import hashlib
    import os

    base = pathlib.Path(
        os.environ.get("AGENTHIVE_HOOK_STATE_DIR")
        or (pathlib.Path.home() / ".local" / "state" / "agenthive"))
    base.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256((cwd or "").encode()).hexdigest()[:16]
    return base / f"failures-{key}.json"


def observe_failure(cwd: str, command: str, output: str) -> None:
    """Remember a command that did not work here.

    Only recorded, never proposed on its own. A fact forms when the same
    command fails and a DIFFERENT one succeeds - one failure is a typo,
    a repeated one with a known fix is knowledge."""
    if not command or not _WORTH_LEARNING.search(command):
        return
    if not _NOT_FOUND.search(output or ""):
        return
    path = _state_path(cwd)
    try:
        state = json.loads(path.read_text()) if path.exists() else {}
    except (OSError, json.JSONDecodeError):
        state = {}
    entry = state.setdefault(command.strip()[:200], {"failures": 0})
    entry["failures"] += 1
    entry["last"] = time.time()
    try:
        path.write_text(json.dumps(state)[:200000])
    except OSError:
        pass


def observe_success(cwd: str, command: str) -> dict | None:
    """A command that worked. If something similar kept failing, that
    pair is a fact worth proposing.

    Returns the proposal rather than writing it: a fact enters the graph
    through log_session like anything else, and faces the same review
    gate. Nothing here writes to shared memory directly."""
    if not command or not _WORTH_LEARNING.search(command):
        return None
    path = _state_path(cwd)
    try:
        state = json.loads(path.read_text()) if path.exists() else {}
    except (OSError, json.JSONDecodeError):
        return None

    tool = _WORTH_LEARNING.search(command).group(1)
    for failed, entry in list(state.items()):
        if entry.get("failures", 0) < 2:
            continue
        if tool not in failed or failed.strip() == command.strip():
            continue
        state.pop(failed, None)
        try:
            path.write_text(json.dumps(state))
        except OSError:
            pass
        return {
            "title": f"How to run {tool} here",
            "body": f"`{command.strip()[:120]}` works here; "
                    f"`{failed.strip()[:60]}` does not.",
            "tags": ["env-fact"],
        }
    return None
