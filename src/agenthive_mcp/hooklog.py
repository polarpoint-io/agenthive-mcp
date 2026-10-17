"""
hooklog.py - the `agenthive-log` console script: a harness lifecycle hook
that writes a memory node when the agent itself didn't.

WHY THIS EXISTS AND WHY IT IS NOT AN MCP TOOL
---------------------------------------------
MCP tools are called by the model, inside its loop. Hooks are shell
commands the harness spawns outside it. So Claude Code's SessionEnd and
Cursor's `stop` cannot reach the log_session MCP tool, however
conveniently it is sitting in the tool list - the two paths do not
connect. A hook needs its own route to the API, which is this.

It lives in agenthive-mcp rather than in a script of its own because
this package is already installed on every harness that has the MCP
server, already holds the HTTP client, and already reads
AGENTHIVE_URL/AGENTHIVE_TOKEN/AGENTHIVE_TEAM_ID from the environment.
A standalone script would mean a second thing to deploy and a second
copy of the same credential.

WHAT IT DELIBERATELY DOES NOT DO
--------------------------------
A hook receives metadata, not understanding. It knows a session ended;
it does not know what was learned. Writing a confident-looking memory
node from that would fill the review queue with content-free entries and
devalue the queue itself - which is the one thing in AgentHive that has
to stay worth a human's attention.

So this is a BACKSTOP, not a replacement for the model calling
log_session properly:

  - If log_session already succeeded recently in this session, this
    writes nothing at all. The model did the job; it did it better.
  - If it did not - the session crashed, hit the context limit, or the
    terminal was closed - this writes one short node recording that a
    session happened and was never summarised, tagged so it is obvious
    at review time.

The point is that the GAP becomes visible, not that the gap gets filled
with filler.

TRANSCRIPT CONTENT IS OPT-IN, OFF BY DEFAULT
--------------------------------------------
It is technically easy to read the harness transcript and paste an
excerpt into the node body. It is also how you leak a credential into
shared team memory: a transcript contains whatever was pasted into it,
and nothing in it is wrapped in <private> for us. The approval gate
would catch it only after it is already stored and rendered in an
admin's browser.

AGENTHIVE_HOOK_INCLUDE_TRANSCRIPT=true turns it on for anyone who has
decided that trade is right for their team. Default is metadata only.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import subprocess
import sys
import time

from .client import AgentHiveConfigError, AgentHiveError, get_config, request

# Hooks configured in more than one place all run - a `stop` hook present
# in both ~/.cursor/hooks.json and <project>/.cursor/hooks.json fires
# twice for one session. Dedupe on content hash so the second firing is a
# no-op instead of a duplicate node in the review queue.
_DEDUPE_TTL_SECONDS = 900

# How recently a successful log_session counts as "the model already did
# this". Generous on purpose: a false "already done" costs one missing
# backstop node, a false "not done" costs a junk node in the queue, and
# the queue matters more.
_RECENT_LOG_SECONDS = 3600

_SESSION_ID_KEYS = (
    "session_id", "sessionId", "conversation_id", "conversationId",
    "chat_id", "chatId", "thread_id", "threadId",
)
_REASON_KEYS = ("reason", "status", "stop_reason", "stopReason", "trigger")


def state_dir() -> pathlib.Path:
    """XDG-ish state directory, overridable for tests."""
    override = os.environ.get("AGENTHIVE_HOOK_STATE_DIR")
    if override:
        base = pathlib.Path(override)
    else:
        base = pathlib.Path(
            os.environ.get("XDG_STATE_HOME", str(pathlib.Path.home() / ".local" / "state"))
        ) / "agenthive"
    base.mkdir(parents=True, exist_ok=True)
    return base


def mark_logged(session_id: str = "") -> None:
    """Called by the log_session MCP tool on success. This is how the hook
    knows to stay quiet: the tool leaves a timestamp, the hook reads it."""
    try:
        path = state_dir() / "last-log-session"
        path.write_text(json.dumps({"at": time.time(), "session_id": session_id or ""}))
    except OSError:
        pass  # never let bookkeeping break a successful write


def logged_recently(within: int = _RECENT_LOG_SECONDS) -> bool:
    try:
        raw = json.loads((state_dir() / "last-log-session").read_text())
    except (OSError, json.JSONDecodeError):
        return False
    return (time.time() - float(raw.get("at", 0))) < within


def _already_sent(fingerprint: str) -> bool:
    """True when this exact payload was sent inside the dedupe window."""
    path = state_dir() / ("sent-" + fingerprint[:32])
    now = time.time()
    try:
        if path.exists() and (now - path.stat().st_mtime) < _DEDUPE_TTL_SECONDS:
            return True
        path.write_text(str(now))
    except OSError:
        return False  # can't dedupe - prefer a possible duplicate to a lost node
    return False


def _git_context(cwd: str) -> dict:
    """Repo and branch, if we're in a work tree. Cheap, and it's the one
    piece of metadata that makes a backstop node findable later."""
    out = {}
    for key, args in (
        ("repo", ["git", "rev-parse", "--show-toplevel"]),
        ("branch", ["git", "rev-parse", "--abbrev-ref", "HEAD"]),
    ):
        try:
            value = subprocess.run(
                args, cwd=cwd or None, capture_output=True, text=True,
                timeout=3, check=False,
            )
            if value.returncode == 0 and value.stdout.strip():
                out[key] = value.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    if "repo" in out:
        out["repo"] = os.path.basename(out["repo"])
    return out


def _first(payload: dict, keys) -> str:
    for key in keys:
        value = payload.get(key)
        if value:
            return str(value)
    return ""


def _transcript_excerpt(payload: dict, max_chars: int = 2000) -> str:
    """Opt-in only. Reads the tail of a JSONL transcript if the harness
    gave us a path to one. See the module docstring for why this is off
    by default."""
    path = payload.get("transcript_path") or payload.get("transcriptPath")
    if not path or not os.path.isfile(path):
        return ""
    try:
        lines = pathlib.Path(path).read_text(errors="replace").splitlines()[-40:]
    except OSError:
        return ""
    texts = []
    for line in lines:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        content = entry.get("content") or entry.get("text") or ""
        if isinstance(content, list):
            content = " ".join(
                part.get("text", "") for part in content if isinstance(part, dict)
            )
        if isinstance(content, str) and content.strip():
            texts.append(content.strip())
    excerpt = "\n".join(texts)[-max_chars:]
    return excerpt


def build_node(payload: dict) -> dict:
    """Turn a hook payload into the smallest honest memory node."""
    cwd = payload.get("cwd") or payload.get("workspace_path") or os.getcwd()
    event = payload.get("hook_event_name") or payload.get("event") or "session-end"
    reason = _first(payload, _REASON_KEYS)
    session_id = _first(payload, _SESSION_ID_KEYS)
    git = _git_context(cwd)

    where = git.get("repo") or os.path.basename(cwd.rstrip("/")) or "unknown"
    title = f"Unsummarised agent session in {where}"
    if git.get("branch") and git["branch"] not in ("HEAD",):
        title += f" ({git['branch']})"

    preamble = (
        "This session ended without the agent calling log_session, so there "
        "is no account of what it did or learned. Recorded by the "
        "agenthive-log hook so the gap is visible rather than silent."
    )
    lines = [
        preamble,
        "",
        f"- event: {event}",
    ]
    if reason:
        lines.append(f"- reason: {reason}")
    if session_id:
        lines.append(f"- session: {session_id}")
    for key in ("repo", "branch"):
        if git.get(key):
            lines.append(f"- {key}: {git[key]}")
    lines.append(f"- cwd: {cwd}")

    if os.environ.get("AGENTHIVE_HOOK_INCLUDE_TRANSCRIPT", "").lower() == "true":
        excerpt = _transcript_excerpt(payload)
        if excerpt:
            heading = ("Transcript tail "
                       "(opt-in via AGENTHIVE_HOOK_INCLUDE_TRANSCRIPT):")
            lines += ["", heading, "", excerpt]

    return {
        "title": title,
        "body": "\n".join(lines),
        # Reserved tags, so this is filterable at review time and can never
        # be auto-approved into the graph by a broad rule. "unsummarised"
        # is the one a reviewer will want to bulk-reject.
        "tags": ["unsummarised", "hook-captured"],
        "links": [],
    }


def main() -> int:
    """Entry point. ALWAYS exits 0.

    A hook that fails must not break the user's session, and a memory
    system that can take a harness down is worse than no memory system.
    Problems go to stderr, where the harness surfaces them, and the exit
    code stays clean."""
    raw = ""
    try:
        if not sys.stdin.isatty():
            raw = sys.stdin.read()
    except OSError:
        pass

    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    if logged_recently():
        print("agenthive-log: log_session already recorded for this session, "
              "nothing to back up", file=sys.stderr)
        return 0

    node = build_node(payload)
    fingerprint = hashlib.sha256(
        (node["title"] + "\x00" + node["body"]).encode("utf-8")
    ).hexdigest()
    if _already_sent(fingerprint):
        print("agenthive-log: identical payload already sent, skipping "
              "(hooks in multiple config locations both fire)", file=sys.stderr)
        return 0

    try:
        _, _, team_id = get_config()
        result = request("POST", f"/teams/{team_id}/memory", body=node)
        print(f"agenthive-log: wrote {result.get('id', '?')} "
              f"({result.get('status', '?')})", file=sys.stderr)
    except AgentHiveConfigError as e:
        print(f"agenthive-log: not configured, skipping - {e}", file=sys.stderr)
    except AgentHiveError as e:
        print(f"agenthive-log: could not write - {e}", file=sys.stderr)
    except Exception as e:  # noqa: BLE001 - a hook must never raise
        print(f"agenthive-log: unexpected error - {e!r}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
