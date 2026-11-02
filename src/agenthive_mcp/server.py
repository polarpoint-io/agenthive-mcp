"""agenthive-mcp - a thin MCP (Model Context Protocol) server exposing
AgentHive's member-level calls - retrieve_context, retrieve_index,
fetch_nodes, report_gaps, log_session, create_agent, list_agents,
create_task, and list_tasks - as native tools
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
auto-approve rules, and the link/tier/rule suggestions the
memory-processing layer produces) are deliberately left out: they exist to let a human
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
from .client import SESSION_ID as _SESSION_ID
from .client import request as _request
from .hooklog import mark_logged as _mark_logged

logger = logging.getLogger("agenthive_mcp")

mcp = FastMCP(
    name="agenthive",
    instructions=(
        "Shared, reviewed memory for a team of coding agents. Call "
        "retrieve_context before starting work and log_session once you're "
        "done - or retrieve_index then fetch_nodes when the topic is broad "
        "and you only need part of what comes back. Requires AGENTHIVE_URL/AGENTHIVE_TOKEN/AGENTHIVE_TEAM_ID env "
        "vars set to your own personal AgentHive token."
    ),
)


@mcp.tool()
def retrieve_context(anchor: str, hops: int = 2, hub_cutoff: int = 15) -> dict:
    """Retrieve this team's reviewed memory around a topic.

    Call once at the start of a session so you inherit what teammates
    already learned. `anchor` is the topic to search around.

    Large neighbourhoods come back as an index (titles and per-node
    costs, no bodies) - follow with fetch_nodes for the ones worth
    reading."""
    _, _, team_id = _get_config()
    return _request(
        "GET", f"/teams/{team_id}/memory/retrieve",
        query={"anchor": anchor, "hops": hops, "hub_cutoff": hub_cutoff,
               "session_id": _SESSION_ID},
    )


@mcp.tool()
def retrieve_index(
    anchor: str,
    hops: int = 2,
    hub_cutoff: int = 15,
    tags: list[str] | None = None,
) -> dict:
    """Preview what retrieve_context would return, without the bodies.

    Returns ids, titles, tiers, tags and what each body would cost.
    Follow with fetch_nodes. `tags` narrows it; the reserved ones are
    decision, bugfix, security_alert, sensitive."""
    _, _, team_id = _get_config()
    query: dict = {
        "anchor": anchor, "hops": hops, "hub_cutoff": hub_cutoff, "mode": "index",
        "session_id": _SESSION_ID,
    }
    if tags:
        query["tags"] = ",".join(tags)
    return _request("GET", f"/teams/{team_id}/memory/retrieve", query=query)


@mcp.tool()
def fetch_nodes(ids: list[str]) -> dict:
    """Fetch full bodies for specific node ids, in ONE call.

    Ids you are not entitled to come back in `missing` rather than as an
    error - that is the review gate working."""
    _, _, team_id = _get_config()
    return _request("POST", f"/teams/{team_id}/memory/fetch", body={"ids": ids})


@mcp.tool()
def report_gaps(min_count: int = 2) -> dict:
    """What this team keeps searching for and never finds.

    Call at session start. If your work is on this list, your
    log_session closes a standing gap. Entries flagged awaiting_review
    already have an answer stuck in the queue - don't write it twice."""
    _, _, team_id = _get_config()
    return _request("GET", f"/teams/{team_id}/gaps", query={"min_count": min_count})


@mcp.tool()
def confirm_memory(node_id: str, verdict: str, reason: str = "") -> dict:
    """Report on a memory node you actually USED this session.

    `verdict` is "confirmed" or "contradicted". A contradiction never
    deletes: the note stops being promoted and goes to a human. Counted
    once per person."""
    _, _, team_id = _get_config()
    return _request(
        "POST", f"/teams/{team_id}/memory/{node_id}/feedback",
        body={"verdict": verdict, "reason": reason},
    )


@mcp.tool()
def log_session(
    title: str,
    body: str,
    tags: list[str] | None = None,
    links: list[str] | None = None,
    agent_id: str | None = None,
    task_id: str | None = None,
    outcome: str | None = None,
    turns: int | None = None,
) -> dict:
    """Log what this session learned, for the team to reuse.

    Call once at the end. The node starts PENDING and is invisible until
    an admin approves it - that gate is deliberate.

    `outcome` ("succeeded", "partial", "failed") and `turns` are what
    make the team's evidence report possible. Say "failed" when it
    failed: a report nobody is honest in looks like data."""
    _, _, team_id = _get_config()
    result = _request(
        "POST", f"/teams/{team_id}/memory",
        body={
            "title": title, "body": body, "tags": tags or [], "links": links or [],
            "agent_id": agent_id, "task_id": task_id,
            "session_id": _SESSION_ID,
            **({"outcome": outcome} if outcome else {}),
            **({"turns": turns} if turns else {}),
        },
    )
    # Leave a local breadcrumb so the agenthive-log hook knows the model
    # already did this properly and stays quiet instead of writing a
    # content-free backstop node on top of it. See hooklog.py.
    _mark_logged(cwd=os.getcwd())
    return result


# Setup operations: registering an agent identity or a task. A normal
# working session never calls these, yet their definitions were sent on
# every request. Off unless asked for.
_SETUP_TOOLS = os.environ.get("AGENTHIVE_MCP_SETUP_TOOLS", "").lower() in ("1", "true", "yes")
_setup_tool = mcp.tool() if _SETUP_TOOLS else (lambda fn: fn)


@_setup_tool
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


@_setup_tool
def list_agents() -> dict:
    """List the agent identities already registered with this team, e.g.
    to find an existing agent's id instead of creating a duplicate with
    create_agent."""
    _, _, team_id = _get_config()
    return _request("GET", f"/teams/{team_id}/agents")


@_setup_tool
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


@_setup_tool
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
