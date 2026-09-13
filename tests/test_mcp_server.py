"""
Drives mcp_server.py as a real subprocess over real MCP stdio, using a
real mcp.ClientSession - nothing mocked at the protocol layer. The
AgentHive side is a small local fake (fake_agenthive.py), not the real
service - see that file's docstring for why, and README.md for how to
also run against a real one manually.
"""
import json
import os
import sys

import anyio
import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from fake_agenthive import FakeAgentHive

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKEN = "test-token-abc123"
TEAM_ID = "team-xyz"


@pytest.fixture()
def fake_server():
    server = FakeAgentHive(token=TOKEN, team_id=TEAM_ID)
    try:
        yield server
    finally:
        server.close()


def _server_params(base_url: str, token: str = TOKEN, team_id: str = TEAM_ID) -> StdioServerParameters:
    env = dict(os.environ)
    if token is not None:
        env["AGENTHIVE_TOKEN"] = token
    else:
        env.pop("AGENTHIVE_TOKEN", None)
    env["AGENTHIVE_URL"] = base_url
    env["AGENTHIVE_TEAM_ID"] = team_id
    return StdioServerParameters(command=sys.executable, args=["mcp_server.py"], cwd=ROOT, env=env)


def _result_json(result):
    text = "".join(getattr(block, "text", "") for block in result.content)
    return json.loads(text)


def test_tools_are_discoverable(fake_server):
    async def run():
        async with stdio_client(_server_params(fake_server.base_url)) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                return {t.name for t in tools.tools}

    names = anyio.run(run)
    assert {
        "retrieve_context", "log_session",
        "create_agent", "list_agents",
        "create_task", "list_tasks",
    } <= names


def test_log_then_retrieve_round_trips_through_the_real_wire_format(fake_server):
    """log_session writes pending; retrieve_context only sees it after
    approval - the fake enforces the same shape as the real service."""

    async def write():
        async with stdio_client(_server_params(fake_server.base_url)) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(
                    "log_session",
                    {"title": "MCP round trip", "body": "written via MCP", "tags": ["t"]},
                )
                assert not result.isError, result.content
                return _result_json(result)

    written = anyio.run(write)
    assert written["status"] == "pending"

    async def retrieve_before_approval():
        async with stdio_client(_server_params(fake_server.base_url)) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("retrieve_context", {"anchor": "MCP round trip"})
                return _result_json(result)

    before = anyio.run(retrieve_before_approval)
    assert before["neighborhood"] == []

    fake_server.nodes[written["id"]]["status"] = "approved"

    async def retrieve_after_approval():
        async with stdio_client(_server_params(fake_server.base_url)) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("retrieve_context", {"anchor": "MCP round trip"})
                return _result_json(result)

    after = anyio.run(retrieve_after_approval)
    assert [n["title"] for n in after["neighborhood"]] == ["MCP round trip"]


def test_create_agent_then_list_agents_round_trips(fake_server):
    async def create():
        async with stdio_client(_server_params(fake_server.base_url)) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(
                    "create_agent",
                    {"name": "bug-fix engineer", "description": "fixes prod incidents"},
                )
                assert not result.isError, result.content
                return _result_json(result)

    created = anyio.run(create)
    assert created["name"] == "bug-fix engineer"
    assert created["id"]

    async def list_them():
        async with stdio_client(_server_params(fake_server.base_url)) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("list_agents", {})
                return _result_json(result)

    listed = anyio.run(list_them)
    assert [a["id"] for a in listed["agents"]] == [created["id"]]


def test_create_task_then_list_tasks_round_trips(fake_server):
    async def create():
        async with stdio_client(_server_params(fake_server.base_url)) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(
                    "create_task",
                    {"name": "migrate to RKE2", "description": "cluster migration"},
                )
                assert not result.isError, result.content
                return _result_json(result)

    created = anyio.run(create)
    assert created["name"] == "migrate to RKE2"
    assert created["id"]

    async def list_them():
        async with stdio_client(_server_params(fake_server.base_url)) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("list_tasks", {})
                return _result_json(result)

    listed = anyio.run(list_them)
    assert [t["id"] for t in listed["tasks"]] == [created["id"]]


def test_log_session_attaches_agent_and_task_ids(fake_server):
    async def run():
        async with stdio_client(_server_params(fake_server.base_url)) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(
                    "log_session",
                    {
                        "title": "scoped session",
                        "body": "linked to an agent and a task",
                        "agent_id": "agent-1",
                        "task_id": "task-1",
                    },
                )
                assert not result.isError, result.content
                return _result_json(result)

    written = anyio.run(run)
    assert written["agent_id"] == "agent-1"
    assert written["task_id"] == "task-1"


def test_wrong_token_surfaces_the_servers_own_error_message(fake_server):
    async def run():
        async with stdio_client(_server_params(fake_server.base_url, token="wrong-token")) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await session.call_tool("retrieve_context", {"anchor": "anything"})

    result = anyio.run(run)
    assert result.isError
    text = " ".join(getattr(block, "text", "") for block in result.content)
    assert "invalid, expired, or revoked API key" in text


def test_missing_token_gives_a_clear_error_not_a_crash(fake_server):
    async def run():
        async with stdio_client(_server_params(fake_server.base_url, token=None)) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await session.call_tool("retrieve_context", {"anchor": "anything"})

    result = anyio.run(run)
    assert result.isError
    text = " ".join(getattr(block, "text", "") for block in result.content)
    assert "AGENTHIVE_TOKEN" in text


def test_unreachable_host_gives_a_clear_error_not_a_hang():
    async def run():
        # Nothing listening on this port - the point is a fast, clear
        # error rather than urllib's default long timeout or a crash.
        async with stdio_client(_server_params("http://127.0.0.1:1")) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await session.call_tool("retrieve_context", {"anchor": "anything"})

    result = anyio.run(run)
    assert result.isError
    text = " ".join(getattr(block, "text", "") for block in result.content)
    assert "could not reach" in text
