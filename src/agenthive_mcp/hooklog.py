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
server and already holds the HTTP client. Note that it does NOT
inherit the MCP server's environment: a client's `env` block configures
that subprocess only, and the harness spawns this hook separately. See
README.md's "Giving the hook its credentials".

WHAT IT DELIBERATELY DOES NOT DO
--------------------------------
A hook receives metadata, not understanding. It knows a session ended;
it does not know what was learned. Writing a confident-looking memory
node from that would fill the review queue with content-free entries and
devalue the queue itself - which is the one thing in AgentHive that has
to stay worth a human's attention.

So this is a BACKSTOP, not a replacement for the model calling
log_session properly:

  - If log_session already succeeded for this destination and project,
    this writes nothing at all. The model did the job; it did it better.
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

import errno
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import time

from .client import AgentHiveConfigError, AgentHiveError, get_config, request

# How long a completed send suppresses an identical later firing. Hooks
# configured in more than one place all run - a `stop` hook present in
# both ~/.cursor/hooks.json and <project>/.cursor/hooks.json fires twice
# for one session.
_DEDUPE_TTL_SECONDS = 900

# How long an in-flight claim is believed before it is treated as
# abandoned (process killed mid-POST). Must comfortably exceed the
# client's 10s HTTP timeout; short enough that a crash doesn't suppress
# the next session's backstop.
_CLAIM_STALE_SECONDS = 60

# How recently a successful log_session counts as "the model already did
# this", WITHIN one destination and project (see _scope_key). Generous on
# purpose: a false "already done" costs one missing backstop node, a
# false "not done" costs a junk node in the queue, and the queue matters
# more.
_RECENT_LOG_SECONDS = 3600

_SESSION_ID_KEYS = (
    "session_id", "sessionId", "conversation_id", "conversationId",
    "chat_id", "chatId", "thread_id", "threadId",
)
_REASON_KEYS = ("reason", "status", "stop_reason", "stopReason", "trigger")


def state_dir() -> pathlib.Path:
    """XDG-ish state directory, overridable for tests.

    Raises OSError if the path is unusable (e.g. a regular file sits
    there). Every caller treats that as "no state available" rather than
    propagating - see _safe() and main()."""
    override = os.environ.get("AGENTHIVE_HOOK_STATE_DIR")
    if override:
        base = pathlib.Path(override)
    else:
        base = pathlib.Path(
            os.environ.get("XDG_STATE_HOME", str(pathlib.Path.home() / ".local" / "state"))
        ) / "agenthive"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _git_toplevel(cwd: str) -> str:
    try:
        done = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd or None, capture_output=True, text=True, timeout=3, check=False,
        )
        if done.returncode == 0 and done.stdout.strip():
            return done.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return cwd or ""


def _scope_key(cwd: str = "") -> str:
    """Identify "this destination, this project" so a write for one team
    or repo cannot suppress the backstop for another.

    A harness session id would be the natural key, but the MCP server has
    no access to one - it is spawned over stdio and the protocol carries
    no session identifier. The best shared key both sides CAN compute is
    (base url, team id, git top level), which is what this is.

    Residual limitation, deliberately accepted: two concurrent sessions
    in the same repo against the same team alias to one key, so a write
    in either suppresses the other's backstop. And if the MCP server's
    cwd differs from the hook payload's cwd, the keys differ and you get
    one spare backstop node - a reviewable nuisance, not a loss."""
    try:
        base_url, _, team_id = get_config()
    except AgentHiveConfigError:
        base_url, team_id = "", ""
    project = _git_toplevel(cwd or os.getcwd())
    raw = f"{base_url}|{team_id}|{project}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def mark_logged(session_id: str = "", cwd: str = "") -> None:
    """Called by the log_session MCP tool on success. This is how the
    hook knows to stay quiet: the tool leaves a scoped timestamp, the
    hook reads it back under the same scope."""
    try:
        path = state_dir() / ("logged-" + _scope_key(cwd))
        path.write_text(json.dumps({
            "at": time.time(),
            "session_id": session_id or "",
        }))
    except OSError:
        pass  # never let bookkeeping break a successful write


def logged_recently(within: int = _RECENT_LOG_SECONDS, cwd: str = "") -> bool:
    """True when log_session already succeeded for THIS destination and
    project inside the window."""
    try:
        path = state_dir() / ("logged-" + _scope_key(cwd))
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, ValueError):
        return False
    try:
        return (time.time() - float(raw.get("at", 0))) < within
    except (TypeError, ValueError):
        return False


def _claim_path(fingerprint: str) -> pathlib.Path:
    return state_dir() / ("sent-" + fingerprint[:32])


def claim_send(fingerprint: str):
    """Atomically claim the right to send this payload.

    Returns the claim path when the caller won it, or None when another
    process holds it or it was already delivered. The claim is an
    O_EXCL create, so two concurrent hooks cannot both win.

    A claim records only that a send is IN FLIGHT. It becomes a record of
    delivery when finish_send() rewrites it, and is removed by
    release_send() on failure - so a failed POST does not suppress the
    retry, which an unconditional write-before-send would have done."""
    path = _claim_path(fingerprint)
    now = time.time()

    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        try:
            existing = json.loads(path.read_text())
            state = existing.get("state")
            age = now - float(existing.get("at", 0))
        except (OSError, json.JSONDecodeError, ValueError, TypeError):
            # Unreadable claim: treat as abandoned and take it over.
            state, age = "inflight", _CLAIM_STALE_SECONDS + 1

        if state == "sent" and age < _DEDUPE_TTL_SECONDS:
            return None  # genuinely already delivered
        if state == "inflight" and age < _CLAIM_STALE_SECONDS:
            return None  # a concurrent hook is mid-POST; don't double-send
        try:
            path.write_text(json.dumps({"state": "inflight", "at": now}))
        except OSError:
            return None
        return path
    except OSError:
        return None  # can't claim at all - caller proceeds unprotected

    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(json.dumps({"state": "inflight", "at": now}))
    except OSError:
        return None
    return path


def finish_send(path) -> None:
    """Promote an in-flight claim to a delivery record."""
    if path is None:
        return
    try:
        path.write_text(json.dumps({"state": "sent", "at": time.time()}))
    except OSError:
        pass


def release_send(path) -> None:
    """Drop a claim after a failed send, so the next firing can retry."""
    if path is None:
        return
    try:
        path.unlink()
    except OSError:
        pass


def _git_context(cwd: str) -> dict:
    """Repo and branch, if we're in a work tree. Cheap, and it's the one
    piece of metadata that makes a backstop node findable later."""
    out = {}
    for key, args in (
        ("repo", ["git", "rev-parse", "--show-toplevel"]),
        ("branch", ["git", "rev-parse", "--abbrev-ref", "HEAD"]),
    ):
        try:
            done = subprocess.run(
                args, cwd=cwd or None, capture_output=True, text=True,
                timeout=3, check=False,
            )
            if done.returncode == 0 and done.stdout.strip():
                out[key] = done.stdout.strip()
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


def _str_field(payload: dict, *keys) -> str:
    """Payload values come from a harness we don't control, so a field
    that should be a path might be a number, a list, or null. Coerce
    rather than trusting the type."""
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
        if value not in (None, "", [], {}):
            return str(value)
    return ""


def _transcript_excerpt(payload: dict, max_chars: int = 2000) -> str:
    """Opt-in only. Reads the tail of a JSONL transcript if the harness
    gave us a path to one. See the module docstring for why this is off
    by default."""
    path = _str_field(payload, "transcript_path", "transcriptPath")
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
        if not isinstance(entry, dict):
            continue
        content = entry.get("content") or entry.get("text") or ""
        if isinstance(content, list):
            content = " ".join(
                part.get("text", "") for part in content if isinstance(part, dict)
            )
        if isinstance(content, str) and content.strip():
            texts.append(content.strip())
    return "\n".join(texts)[-max_chars:]


def build_node(payload: dict) -> dict:
    """Turn a hook payload into the smallest honest memory node."""
    cwd = _str_field(payload, "cwd", "workspace_path") or os.getcwd()
    event = _str_field(payload, "hook_event_name", "event") or "session-end"
    reason = _first(payload, _REASON_KEYS)
    session_id = _first(payload, _SESSION_ID_KEYS)
    git = _git_context(cwd)

    where = git.get("repo") or os.path.basename(cwd.rstrip("/")) or "unknown"
    title = f"Unsummarised agent session in {where}"
    if git.get("branch") and git["branch"] != "HEAD":
        title += f" ({git['branch']})"

    preamble = (
        "This session ended without the agent calling log_session, so there "
        "is no account of what it did or learned. Recorded by the "
        "agenthive-log hook so the gap is visible rather than silent."
    )
    lines = [preamble, "", f"- event: {event}"]
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


def _run() -> None:
    """The actual work. Every failure mode here is caught by main()."""
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

    cwd = _str_field(payload, "cwd", "workspace_path")

    if logged_recently(cwd=cwd):
        print("agenthive-log: log_session already recorded for this project, "
              "nothing to back up", file=sys.stderr)
        return

    node = build_node(payload)
    fingerprint = hashlib.sha256(
        (_scope_key(cwd) + "\x00" + node["title"] + "\x00" + node["body"]).encode("utf-8")
    ).hexdigest()

    claim = claim_send(fingerprint)
    if claim is None and _claim_path_exists(fingerprint):
        print("agenthive-log: identical payload already sent or in flight, "
              "skipping (hooks in multiple config locations both fire)",
              file=sys.stderr)
        return

    try:
        _, _, team_id = get_config()
        result = request("POST", f"/teams/{team_id}/memory", body=node)
    except AgentHiveConfigError as e:
        release_send(claim)
        print(f"agenthive-log: not configured, skipping - {e}", file=sys.stderr)
        return
    except AgentHiveError as e:
        release_send(claim)
        print(f"agenthive-log: could not write - {e}", file=sys.stderr)
        return
    except Exception:
        # Release the claim so a retry isn't suppressed, then let main()'s
        # catch-all turn this into a clean exit.
        release_send(claim)
        raise

    finish_send(claim)
    print(f"agenthive-log: wrote {result.get('id', '?')} "
          f"({result.get('status', '?')})", file=sys.stderr)


def _claim_path_exists(fingerprint: str) -> bool:
    """Distinguish "someone else holds the claim" from "state is
    unusable". Only the former is a reason to stay silent."""
    try:
        return _claim_path(fingerprint).exists()
    except OSError:
        return False


def main() -> int:
    """Entry point. ALWAYS exits 0.

    A hook that fails must not break the user's session, and a memory
    system that can take the harness down is worse than no memory
    system. The catch-all wraps the WHOLE workflow, not just the HTTP
    call: an unusable state directory, a harness payload with
    unexpected types, and a bookkeeping error are all as capable of
    raising as the network is. Problems go to stderr, where the harness
    surfaces them; the exit code stays clean."""
    try:
        _run()
    except OSError as e:
        if e.errno in (errno.ENOSPC, errno.EACCES, errno.EEXIST, errno.ENOTDIR):
            print(f"agenthive-log: state directory unusable, skipping - {e}",
                  file=sys.stderr)
        else:
            print(f"agenthive-log: i/o error, skipping - {e}", file=sys.stderr)
    except Exception as e:  # noqa: BLE001 - a hook must never raise
        print(f"agenthive-log: unexpected error, skipping - {e!r}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
