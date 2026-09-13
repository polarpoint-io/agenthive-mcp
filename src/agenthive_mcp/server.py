"""agenthive-mcp - a thin MCP (Model Context Protocol) server exposing
AgentHive's member-level calls - retrieve_context, log_session,
create_agent, list_agents, create_task, and list_tasks - as native tools
for Cursor, Claude Code, and any other MCP client.

This is deliberately a standalone, self-contained wrapper: it makes its
own plain HTTP calls to a running AgentHive service rather than importing
anything from the agenthive repo, so this repo has no dependency on that
one at build, install, or test time - only a runtime one, on the base URL
of a service you already have running (see
https://github.com/polarpoint-io/agenthive).

It holds no state and makes no decisions of its own - every tool call is
a pass-through to the AgentHive HTTP API using the caller's own personal
token from AGENTHIVE_TOKEN. The member/admin permission split and the
approval gate (a logged session starts PENDING until reviewed) are both
enforced entirely server-side; this process can do exactly what that
token already lets it do over the API directly, nothing more. See
AgentHive's ADR.md ("Why not build an LLM-request proxy" and
"Authentication") for the reasoning this follows.

Only [member]-role calls are exposed - the same tier retrieve_context and
log_session already sit at (see AgentHive's server.py route table).
Admin-only calls (list_pending/approve/reject, user management,
auto-approve rules) are deliberately left out: they exist to let a human
operate the review gate from the review UI or an admin script, and
turning them into agent-callable tools would let an agent approve or
reject its own (or another agent's) pending memory, which defeats the
point of the gate. See AgentHive's ADR.md for why that gate exists.

Transport selection (env vars), matching the rest of the org's MCP
servers (see polarpoint-io/snyk-mcp):
    TRANSPORT=stdio (default)   - MCP stdio transport, spawned by your IDE
    TRANSPORT=http              - SSE/HTTP transport on HTTP_HOST:HTTP_PORT
"""

from __future__ import annotations

import logging
import os

from mcp.server.fastmcp import FastMCP

from .client import get_config as _get_config
from .client import request as _request

logger = logging.getLogger("agenthive_mcp")

mcp = FastMCP(
    name="agenthive",
    instructions=(
        "Shared, reviewed memory for a team of coding agents. Call "
        "retrieve_context before starting work and log_session once you're "
        "done. Requires AGENTHIVE_URL/AGENTHIVE_TOKEN/AGENTHIVE_TEAM_ID env "
        "vars set to your own personal AgentHive token."
    ),
)


@mcp.tool()
def retrieve_context(anchor: str, hops: int = 2, hub_cutoff: int = 15) -> dict:
    """Retrieve a bounded neighborhood of this team's reviewed, approved
    memory around a topic. Call this once at the start of a session,
    before starting work, so you inherit what teammates already learned
    instead of rediscovering it. `anchor` is the topic/title to search
    around (e.g. "Postgres connection pool exhaustion"); `hops` bounds how
    far the traversal spreads from it (default 2); `hub_cutoff` stops
    traversal through overly-connected "hub" nodes so one popular node
    doesn't pull in the whole graph. Returns a `neighborhood` list of
    memory nodes plus `approx_tokens`, what folding them into context
    would actually cost."""
    _, _, team_id = _get_config()
    return _request(
        "GET", f"/teams/{team_id}/memory/retrieve",
        query={"anchor": anchor, "hops": hops, "hub_cutoff": hub_cutoff},
    )


@mcp.tool()
def log_session(
    title: str,
    body: str,
    tags: list[str] | None = None,
    links: list[str] | None = None,
    agent_id: str | None = None,
    task_id: str | None = None,
) -> dict:
    """Log what happened this session as a memory node for the team to
    reuse. Call this once at the end of a session that learned something
    worth keeping - a root cause, a fix, a decision, a gotcha. The node
    starts PENDING and is invisible to retrieve_context until an admin
    approves it (or it matches an auto-approve rule) - this is a
    deliberate review gate, not a bug. `title` is a short, searchable
    summary (this is what future retrieve_context anchors match against);
    `body` is the actual content; `tags` and `links` are optional and
    help retrieval and review. `agent_id`/`task_id` are optional ids from
    create_agent/create_task - attaching them lets an admin auto-approve
    by agent (see create_auto_approve_rule, admin-only) and lets future
    retrieval be scoped to a task."""
    _, _, team_id = _get_config()
    return _request(
        "POST", f"/teams/{team_id}/memory",
        body={
            "title": title, "body": body, "tags": tags or [], "links": links or [],
            "agent_id": agent_id, "task_id": task_id,
        },
    )


@mcp.tool()
def create_agent(name: str, description: str = "", system_prompt: str = "") -> dict:
    """Register an agent identity with the team, so logged sessions can be
    attributed to it (pass the returned id as log_session's `agent_id`)
    and so an admin can target it with an auto-approve rule. `name` is
    how it shows up in the review UI; `description` and `system_prompt`
    are optional context for reviewers."""
    _, _, team_id = _get_config()
    return _request(
        "POST", f"/teams/{team_id}/agents",
        body={"name": name, "description": description, "system_prompt": system_prompt},
    )


@mcp.tool()
def list_agents() -> dict:
    """List the agent identities already registered with this team, e.g.
    to find an existing agent's id instead of creating a duplicate with
    create_agent."""
    _, _, team_id = _get_config()
    return _request("GET", f"/teams/{team_id}/agents")


@mcp.tool()
def create_task(name: str, description: str = "") -> dict:
    """Register a task with the team, so logged sessions can be scoped to
    it (pass the returned id as log_session's `task_id`) - useful when
    several sessions across one piece of work should be filterable
    together later. `name` is how it shows up in the review UI;
    `description` is optional context."""
    _, _, team_id = _get_config()
    return _request(
        "POST", f"/teams/{team_id}/tasks",
        body={"name": name, "description": description},
    )


@mcp.tool()
def list_tasks() -> dict:
    """List the tasks already registered with this team, e.g. to find an
    existing task's id instead of creating a duplicate with create_task."""
    _, _, team_id = _get_config()
    return _request("GET", f"/teams/{team_id}/tasks")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    """Console entrypoint. Selects transport based on TRANSPORT env var,
    same convention as the rest of the org's MCP servers."""
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    transport = os.environ.get("TRANSPORT", "stdio").lower()
    if transport == "stdio":
        logger.info("Starting agenthive-mcp on stdio transport")
        mcp.run(transport="stdio")
    elif transport in ("http", "sse"):
        host = os.environ.get("HTTP_HOST", "0.0.0.0")
        port = int(os.environ.get("HTTP_PORT", "8000"))
        mcp.settings.host = host
        mcp.settings.port = port
        logger.info("Starting agenthive-mcp on SSE transport at %s:%d", host, port)
        mcp.run(transport="sse")
    else:
        raise SystemExit(f"Unknown TRANSPORT={transport!r}. Use 'stdio' or 'http'.")


if __name__ == "__main__":
    main()
