"""
The hook entry point: `agenthive-hook`.

One command, every harness, every event. It reads the event, normalises
it, and routes to whichever mechanism cares. Each mechanism is wrapped
so that a bug in one cannot affect the others or the session.

Always exits 0. Always.
"""

from __future__ import annotations

import os
import sys

from . import facts, hooks, loopguard, toolprune
from .client import SESSION_ID


def _enabled(name: str, default: bool = True) -> bool:
    raw = os.environ.get(f"AGENTHIVE_{name.upper()}")
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def on_session_start(event: dict) -> str:
    """Inject what the team already knows about working here.

    This is the token-saving path: a fact that replaces fifteen file
    reads pays for itself many times over, which recalling a decision
    does not."""
    if not _enabled("facts"):
        return ""
    block = facts.render(facts.fetch(event["cwd"]))
    if block:
        hooks.note(f"injected {len(block) // 4} tokens of team facts")
    return block


def on_tool_result(event: dict) -> str:
    """Watch for commands that do not work here, and for turns going in
    circles."""
    payload = event["payload"]
    command = str(payload.get("command") or payload.get("tool_input") or "")
    output = str(payload.get("output") or payload.get("tool_response")
                 or payload.get("result") or "")

    if _enabled("facts"):
        ok = payload.get("exit_code", payload.get("exitCode"))
        if ok in (0, None) and command:
            proposal = facts.observe_success(event["cwd"], command)
            if proposal:
                hooks.note(
                    f"learned: {proposal['body']} - log_session it with the "
                    "env-fact tag to share it")
        elif command:
            facts.observe_failure(event["cwd"], command, output)

    if _enabled("loopguard"):
        verdict = loopguard.observe(event["session_id"] or SESSION_ID,
                                    event["cwd"], output)
        if verdict:
            if verdict.get("tell_user"):
                hooks.note(verdict["tell_user"])
            return verdict.get("context") or ""
    return ""


def on_session_end(event: dict) -> str:
    """Report tool usage to the team pool, and hand off to the existing
    session-end backstop."""
    if _enabled("toolprune"):
        payload = event["payload"]
        loaded = payload.get("tools_loaded") or payload.get("available_tools") or []
        used = payload.get("tools_used") or []
        if loaded:
            toolprune.report(loaded, used, event["session_id"])

    from . import hooklog

    hooks.safely(hooklog.main)
    return ""


ROUTES = {
    hooks.SESSION_START: on_session_start,
    hooks.TOOL_RESULT: on_tool_result,
    hooks.SESSION_END: on_session_end,
}


def main() -> int:
    try:
        event = hooks.read_event()
        handler = ROUTES.get(event["event"])
        if handler:
            context = hooks.safely(handler, event)
            if context:
                hooks.emit(context=context)
    except Exception as e:  # noqa: BLE001 - the outermost net
        hooks.note(f"unexpected error, session unaffected - {e!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
