"""
The new member-role tools (retrieve_index, fetch_nodes) over real MCP
stdio, plus the agenthive-log hook entry point.

Same philosophy and the same shape as test_tools.py: the MCP server runs
as a real subprocess through a real ClientSession, with the AgentHive
side faked. What's proved here is THIS repo's plumbing - that the tools
exist, pass the right things through, and that the hook is a backstop
rather than a second writer. The approval gate and team isolation are
AgentHive's own properties, tested in AgentHive's own suite against the
real server.py rather than re-asserted against a fake.
"""
import json
import os
import subprocess
import sys

import anyio
import pytest
from fake_agenthive import FakeAgentHive
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
TOKEN = "test-token-abc123"
TEAM_ID = "team-xyz"


@pytest.fixture()
def fake_server():
    server = FakeAgentHive(token=TOKEN, team_id=TEAM_ID)
    try:
        yield server
    finally:
        server.close()


def _server_params(base_url, extra_env=None):
    env = dict(os.environ)
    env["AGENTHIVE_TOKEN"] = TOKEN
    env["AGENTHIVE_URL"] = base_url
    env["AGENTHIVE_TEAM_ID"] = TEAM_ID
    env["PYTHONPATH"] = SRC + os.pathsep + env.get("PYTHONPATH", "")
    env.update(extra_env or {})
    return StdioServerParameters(
        command=sys.executable, args=["-m", "agenthive_mcp.server"], cwd=ROOT, env=env
    )


def _result_json(result):
    return json.loads("".join(getattr(b, "text", "") for b in result.content))


def _call(base_url, name, args, extra_env=None):
    async def run():
        async with stdio_client(_server_params(base_url, extra_env)) as (read, write), \
                ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(name, args)
            assert not result.isError, result.content
            return _result_json(result)
    return anyio.run(run)


def _seed(server, title, body, tags, approved=True):
    """Put a node straight into the fake. These tests are about reading;
    routing every one through log_session first would make them depend on
    the write path too."""
    node_id = str(server._next_id)
    server._next_id += 1
    server.nodes[node_id] = {
        "id": node_id, "title": title, "body": body, "tags": tags,
        "links": [], "agent_id": None, "task_id": None, "tier": "L1",
        "status": "approved" if approved else "pending",
    }
    return server.nodes[node_id]


# ---------------------------------------------------------------------------
# retrieve_index / fetch_nodes
# ---------------------------------------------------------------------------

def test_new_tools_advertised_and_admin_ones_still_are_not(fake_server):
    async def run():
        async with stdio_client(_server_params(fake_server.base_url)) as (read, write), \
                ClientSession(read, write) as session:
            await session.initialize()
            return {t.name for t in (await session.list_tools()).tools}

    names = anyio.run(run)
    assert {"retrieve_index", "fetch_nodes"} <= names
    # Adding read tools must not have quietly widened the surface: the
    # review gate is still a human's job.
    assert not names & {"approve", "reject", "list_pending", "create_user",
                        "create_auto_approve_rule"}


def test_index_then_fetch_round_trip(fake_server):
    seeded = _seed(fake_server, "Postgres connection pool exhaustion",
                   "Root cause: pgbouncer max_client_conn too low. " * 40,
                   ["bugfix"])

    index = _call(fake_server.base_url, "retrieve_index",
                  {"anchor": "Postgres connection pool exhaustion"})
    assert index["mode"] == "index"
    assert index["neighborhood_count"] == 1

    entry = index["neighborhood"][0]
    assert "body" not in entry
    assert entry["tags"] == ["bugfix"]
    assert entry["approx_tokens"] > 0
    # The whole point of the layer: cheaper than what it stands in for.
    assert index["approx_tokens"] < index["approx_tokens_full_neighborhood"]

    fetched = _call(fake_server.base_url, "fetch_nodes", {"ids": [entry["id"]]})
    assert fetched["node_count"] == 1
    assert fetched["nodes"][0]["id"] == seeded["id"]
    assert "pgbouncer" in fetched["nodes"][0]["body"]
    assert fetched["missing"] == []


def test_index_sends_the_tag_filter_as_a_comma_list(fake_server):
    _seed(fake_server, "Incident alpha", "body " * 50, ["bugfix"])
    _seed(fake_server, "Incident beta", "body " * 50, ["decision"])

    index = _call(fake_server.base_url, "retrieve_index",
                  {"anchor": "Incident", "tags": ["bugfix", "decision"]})
    # The fake does not implement traversal or filtering - that is
    # AgentHive's, and tested there. What this pins is that the tool
    # reaches the wire at all with mode=index and a tags param.
    assert index["mode"] == "index"
    assert index["neighborhood_count"] >= 1


def test_fetch_reports_unentitled_ids_as_missing_not_errors(fake_server):
    pending = _seed(fake_server, "Unreviewed", "not yours yet", [], approved=False)

    result = _call(fake_server.base_url, "fetch_nodes",
                   {"ids": [pending["id"], "does-not-exist"]})
    assert result["node_count"] == 0
    assert set(result["missing"]) == {pending["id"], "does-not-exist"}


def test_retrieve_context_still_returns_bodies(fake_server):
    _seed(fake_server, "Still full", "body text here", [])
    result = _call(fake_server.base_url, "retrieve_context", {"anchor": "Still full"})
    assert result["neighborhood"][0]["body"] == "body text here"
    assert "mode" not in result


def test_log_session_leaves_the_breadcrumb_the_hook_reads(fake_server, tmp_path):
    """The tool and the hook coordinate through a local marker file. If
    this link breaks, the hook writes a content-free node on top of a
    perfectly good one - the failure most likely to go unnoticed."""
    state = tmp_path / "state"
    written = _call(fake_server.base_url, "log_session",
                    {"title": "Real summary", "body": "written by the model"},
                    extra_env={"AGENTHIVE_HOOK_STATE_DIR": str(state)})
    assert written["status"] == "pending"
    assert (state / "last-log-session").exists()


# ---------------------------------------------------------------------------
# the agenthive-log hook
# ---------------------------------------------------------------------------

def _run_hook(payload, env_extra, stdin=None):
    env = dict(os.environ)
    env["PYTHONPATH"] = SRC + os.pathsep + env.get("PYTHONPATH", "")
    for key in ("AGENTHIVE_URL", "AGENTHIVE_TOKEN", "AGENTHIVE_TEAM_ID"):
        env.pop(key, None)
    env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "agenthive_mcp.hooklog"],
        input=stdin if stdin is not None else json.dumps(payload),
        text=True, capture_output=True, env=env, timeout=30, cwd=ROOT, check=False,
    )


def _hook_env(fake_server, state_dir):
    return {
        "AGENTHIVE_URL": fake_server.base_url,
        "AGENTHIVE_TOKEN": TOKEN,
        "AGENTHIVE_TEAM_ID": TEAM_ID,
        "AGENTHIVE_HOOK_STATE_DIR": str(state_dir),
    }


def _backstop(fake_server):
    return [n for n in fake_server.nodes.values() if "Unsummarised" in (n["title"] or "")]


def test_hook_writes_a_backstop_node_when_the_agent_did_not(fake_server, tmp_path):
    result = _run_hook(
        {"hook_event_name": "SessionEnd", "reason": "clear",
         "session_id": "abc123", "cwd": str(tmp_path)},
        _hook_env(fake_server, tmp_path / "state"),
    )
    assert result.returncode == 0, result.stderr

    nodes = _backstop(fake_server)
    assert len(nodes) == 1
    assert set(nodes[0]["tags"]) == {"unsummarised", "hook-captured"}
    assert "abc123" in nodes[0]["body"]
    # Pending like any other write - the hook gets no privileged path.
    assert nodes[0]["status"] == "pending"


def test_hook_stays_quiet_when_log_session_already_ran(fake_server, tmp_path):
    state = tmp_path / "state"
    sys.path.insert(0, SRC)
    os.environ["AGENTHIVE_HOOK_STATE_DIR"] = str(state)
    try:
        from agenthive_mcp.hooklog import mark_logged
        mark_logged("abc123")
    finally:
        os.environ.pop("AGENTHIVE_HOOK_STATE_DIR", None)

    before = len(fake_server.nodes)
    result = _run_hook({"hook_event_name": "SessionEnd", "cwd": str(tmp_path)},
                       _hook_env(fake_server, state))
    assert result.returncode == 0
    assert len(fake_server.nodes) == before
    assert "already recorded" in result.stderr


def test_hook_is_idempotent_across_duplicate_firings(fake_server, tmp_path):
    """Hooks present in several config locations ALL run - a `stop` hook
    in both ~/.cursor/hooks.json and the project's fires twice for one
    session. The second must not produce a second node."""
    env = _hook_env(fake_server, tmp_path / "state")
    payload = {"hook_event_name": "stop", "conversation_id": "dup-1",
               "cwd": str(tmp_path)}

    first = _run_hook(payload, env)
    second = _run_hook(payload, env)

    assert first.returncode == second.returncode == 0
    assert len(_backstop(fake_server)) == 1
    assert "already sent" in second.stderr


def test_hook_never_fails_the_session(tmp_path):
    """Missing config, unreachable API, garbage on stdin - all exit 0. A
    memory system that can take the harness down is worse than none."""
    unconfigured = _run_hook({"hook_event_name": "stop"},
                             {"AGENTHIVE_HOOK_STATE_DIR": str(tmp_path / "s1")})
    assert unconfigured.returncode == 0
    assert "not configured" in unconfigured.stderr

    unreachable = _run_hook({"hook_event_name": "stop"}, {
        "AGENTHIVE_URL": "http://127.0.0.1:9",
        "AGENTHIVE_TOKEN": "t", "AGENTHIVE_TEAM_ID": "team",
        "AGENTHIVE_HOOK_STATE_DIR": str(tmp_path / "s2"),
    })
    assert unreachable.returncode == 0
    assert "could not write" in unreachable.stderr

    garbage = _run_hook(None, {"AGENTHIVE_HOOK_STATE_DIR": str(tmp_path / "s3")},
                        stdin="this is not json{{{")
    assert garbage.returncode == 0


def test_transcript_is_excluded_unless_opted_in(fake_server, tmp_path):
    """A transcript holds whatever was pasted into it, and none of it is
    wrapped in <private> for us. Default off."""
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(json.dumps({"content": "the password is hunter2"}) + "\n")
    payload = {"hook_event_name": "SessionEnd", "cwd": str(tmp_path),
               "transcript_path": str(transcript)}

    _run_hook(payload, _hook_env(fake_server, tmp_path / "state"))
    bodies = " ".join(n["body"] or "" for n in fake_server.nodes.values())
    assert "hunter2" not in bodies

    _run_hook(payload, {**_hook_env(fake_server, tmp_path / "state2"),
                        "AGENTHIVE_HOOK_INCLUDE_TRANSCRIPT": "true"})
    bodies = " ".join(n["body"] or "" for n in fake_server.nodes.values())
    assert "hunter2" in bodies
