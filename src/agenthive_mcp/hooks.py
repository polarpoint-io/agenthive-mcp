"""
One hook implementation, three harnesses.

Claude Code and VS Code share a hook format: JSON on stdin, JSON on
stdout, snake_case event names. Cursor has its own - different event
names, different payload keys - so it gets translated here rather than
duplicated everywhere downstream.

Everything in this module obeys one rule: **a hook must never break the
session**. Exit 0 always, on every path. A memory system that can take
a developer's agent down is worse than no memory system, and the
failure would be blamed on the agent rather than on us.
"""

from __future__ import annotations

import json
import os
import sys

# Normalised events. Everything downstream speaks only these.
SESSION_START = "session_start"
PROMPT = "prompt"
TOOL_RESULT = "tool_result"
SESSION_END = "session_end"

# Claude Code and VS Code: the same names, modulo case.
_SHARED = {
    "sessionstart": SESSION_START,
    "userpromptsubmit": PROMPT,
    "posttooluse": TOOL_RESULT,
    "stop": SESSION_END,
    "sessionend": SESSION_END,
}

# Cursor. `stop` means the same thing; the rest do not line up, and
# beforeSubmitPrompt is the closest it has to a session start - there is
# no event that fires once before the first prompt.
_CURSOR = {
    "beforesubmitprompt": PROMPT,
    "afterfileedit": TOOL_RESULT,
    "aftershellcommand": TOOL_RESULT,
    "stop": SESSION_END,
}

_SESSION_KEYS = ("session_id", "sessionId", "conversation_id", "conversationId",
                 "chat_id", "chatId", "thread_id", "threadId")
_CWD_KEYS = ("cwd", "workspace_path", "workspaceRoots", "workspace_roots")


def detect_host(payload: dict) -> str:
    """Which harness sent this.

    Preferred from AGENTHIVE_HOST because the payloads are genuinely
    ambiguous - `stop` exists in both families and carries no marker.
    Guessing wrong turns a session-end into a no-op silently, so the
    env var is the supported way and the sniff is a fallback."""
    forced = (os.environ.get("AGENTHIVE_HOST") or "").strip().lower()
    if forced in ("claude", "vscode", "cursor", "copilot"):
        return forced
    if "hook_event_name" in payload:
        return "claude"
    if "conversation_id" in payload or "conversationId" in payload:
        return "cursor"
    return "claude"


def _first(payload: dict, keys) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list) and value:
            value = value[0]
        if value:
            return str(value)
    return ""


def normalise(payload: dict) -> dict:
    """One shape, whatever sent it."""
    host = detect_host(payload)
    raw = str(payload.get("hook_event_name") or payload.get("event")
              or payload.get("hookEventName") or "").strip().lower()
    table = _CURSOR if host == "cursor" else _SHARED
    return {
        "host": host,
        "event": table.get(raw, raw or SESSION_END),
        "raw_event": raw,
        "session_id": _first(payload, _SESSION_KEYS),
        "cwd": _first(payload, _CWD_KEYS) or os.getcwd(),
        "payload": payload,
    }


def read_event(stream=None) -> dict:
    """Parse stdin, or return an empty event rather than raising.

    A harness that sends nothing, sends malformed JSON, or sends a list
    where a dict was expected should produce a no-op, not a traceback in
    somebody's terminal."""
    stream = stream or sys.stdin
    raw = ""
    try:
        if not stream.isatty():
            raw = stream.read()
    except (OSError, ValueError):
        pass
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return normalise(payload)


def emit(context: str = "", decision: dict | None = None) -> None:
    """Reply to the harness.

    Claude Code and VS Code read additional context from stdout as JSON;
    Cursor expects its own shape. Both are written, since a key the
    other does not recognise is ignored - and a hook that stays silent
    when it had something to say has done nothing at all."""
    if not context and not decision:
        return
    body = {}
    if context:
        # Claude Code / VS Code
        body["hookSpecificOutput"] = {"additionalContext": context}
        # Cursor
        body["context"] = context
    if decision:
        body.update(decision)
    try:
        sys.stdout.write(json.dumps(body))
        sys.stdout.flush()
    except (OSError, ValueError):
        pass


def note(message: str) -> None:
    """Anything the user should see goes to stderr. stdout is the
    harness's channel and anything stray there is parsed as a reply."""
    try:
        print(f"agenthive: {message}", file=sys.stderr)
    except (OSError, ValueError):
        pass


def safely(fn, *args, **kwargs):
    """Run a handler; swallow anything it throws.

    The whole point of this module. Every handler is called through
    here, so a bug in fact injection cannot stop a session that was
    about to work."""
    try:
        return fn(*args, **kwargs)
    except Exception as e:  # noqa: BLE001 - deliberate: never break the session
        note(f"{getattr(fn, '__name__', 'hook')} failed, continuing without it - {e!r}")
        return None
