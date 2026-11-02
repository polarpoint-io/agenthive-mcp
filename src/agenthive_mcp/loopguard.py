"""
Noticing when a turn is stuck.

A turn repeating the same failure is burning tokens on an approach that
is not going to work. This watches for that pattern and, once, adds a
short note suggesting a different approach.

Three deliberate limits:

- It never stops a turn. Interrupting somebody's agent on a heuristic
  is not a call this gets to make; it says what it sees and how to stop
  it, and the human decides.
- It redirects at most once per turn. A second nudge after the first
  was ignored is noise, and noise trains people to ignore the first.
- It runs entirely on the developer's machine, counting repeated
  failures. No model call, which is what keeps AgentHive's own no-model
  rule intact rather than relocating the violation.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
import time

REPEATS_BEFORE_NUDGE = 3
MAX_NUDGES_PER_TURN = 1
TURN_IDLE_SECONDS = 900

_SIGNAL = re.compile(
    r"(Traceback|AssertionError|SyntaxError|TypeError|ValueError|"
    r"error:|ERROR|FAILED|command not found|No such file)", re.IGNORECASE)


def _path(session_id: str, cwd: str) -> pathlib.Path:
    import os

    base = pathlib.Path(
        os.environ.get("AGENTHIVE_HOOK_STATE_DIR")
        or (pathlib.Path.home() / ".local" / "state" / "agenthive"))
    base.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(f"{session_id}:{cwd}".encode()).hexdigest()[:16]
    return base / f"loop-{key}.json"


def _fingerprint(text: str) -> str:
    """What makes two failures "the same".

    Digits, hex and quoted strings are stripped, so the same assertion
    on a different line number or a different temp path still counts as
    a repeat - which is exactly the case a literal comparison would
    miss, and the most common shape of a real loop."""
    cleaned = re.sub(r"0x[0-9a-f]+|\d+|'[^']*'|\"[^\"]*\"", "", text or "")
    cleaned = " ".join(cleaned.split())[:400]
    return hashlib.sha256(cleaned.encode()).hexdigest()[:16]


def observe(session_id: str, cwd: str, output: str) -> dict | None:
    """Record a tool result. Returns a redirect when a turn is stuck.

    Returns None for everything else, including when it has already
    nudged - the caller does not need to know the difference."""
    if not output or not _SIGNAL.search(output):
        return None

    path = _path(session_id, cwd)
    try:
        state = json.loads(path.read_text()) if path.exists() else {}
    except (OSError, json.JSONDecodeError):
        state = {}

    now = time.time()
    if now - float(state.get("updated") or 0) > TURN_IDLE_SECONDS:
        state = {}          # a long gap means this is a different turn

    fp = _fingerprint(output)
    counts = state.setdefault("counts", {})
    counts[fp] = counts.get(fp, 0) + 1
    state["updated"] = now
    nudges = int(state.get("nudges") or 0)

    redirect = None
    if counts[fp] >= REPEATS_BEFORE_NUDGE and nudges < MAX_NUDGES_PER_TURN:
        state["nudges"] = nudges + 1
        redirect = {
            "repeats": counts[fp],
            "context": (
                f"The same failure has now come back {counts[fp]} times. "
                "Whatever is being retried is not going to start working. "
                "Try a different approach, or say plainly what is blocking "
                "you and stop."),
        }
    elif counts[fp] >= REPEATS_BEFORE_NUDGE * 2 and nudges >= MAX_NUDGES_PER_TURN:
        # Already nudged and still going. Tell the human, do not nudge
        # again and do not stop it - that is their call.
        redirect = {"repeats": counts[fp], "context": "",
                    "tell_user": (
                        f"this turn has hit the same failure {counts[fp]} times "
                        "and was already redirected once. Press Esc to stop it.")}

    try:
        path.write_text(json.dumps(state)[:100000])
    except OSError:
        pass
    return redirect


def reset(session_id: str, cwd: str) -> None:
    try:
        _path(session_id, cwd).unlink()
    except OSError:
        pass
