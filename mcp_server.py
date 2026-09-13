"""
agenthive-mcp - a thin MCP (Model Context Protocol) stdio server exposing
AgentHive's two agent-facing calls, retrieve_context and log_session, as
native tools for Cursor and Claude Code.

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

Runs over stdio - the standard transport for a per-user local MCP server
that an IDE spawns as a subprocess. Reads its target service/team/token
from the environment (AGENTHIVE_URL / AGENTHIVE_TOKEN / AGENTHIVE_TEAM_ID)
rather than a config file, so it's a three-line env block in whatever MCP
client config your IDE uses - see README.md.
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    print(
        "agenthive-mcp needs the `mcp` package: pip install -r requirements.txt",
        file=sys.stderr,
    )
    raise

mcp = FastMCP("agenthive")


class AgentHiveError(RuntimeError):
    """Raised on any non-2xx response from the AgentHive API, with the
    server's own error message rather than a generic HTTP status."""


def _config() -> tuple[str, str, str]:
    missing = [
        name for name in ("AGENTHIVE_URL", "AGENTHIVE_TOKEN", "AGENTHIVE_TEAM_ID")
        if not os.environ.get(name)
    ]
    if missing:
        raise RuntimeError(
            "agenthive-mcp is missing " + ", ".join(missing) + " - set these in "
            "the MCP client config's env block (see README.md)."
        )
    return (
        os.environ["AGENTHIVE_URL"].rstrip("/"),
        os.environ["AGENTHIVE_TOKEN"],
        os.environ["AGENTHIVE_TEAM_ID"],
    )


def _request(method: str, path: str, body: dict | None = None, query: dict | None = None) -> dict:
    base_url, token, _ = _config()
    url = base_url + path
    if query:
        url += "?" + urllib.parse.urlencode(query)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("X-API-Key", token)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read()).get("error", "unknown error")
        except (json.JSONDecodeError, AttributeError):
            detail = e.reason
        raise AgentHiveError(f"{e.code}: {detail}") from None
    except urllib.error.URLError as e:
        raise AgentHiveError(f"could not reach {base_url}: {e.reason}") from None


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
    _, _, team_id = _config()
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
) -> dict:
    """Log what happened this session as a memory node for the team to
    reuse. Call this once at the end of a session that learned something
    worth keeping - a root cause, a fix, a decision, a gotcha. The node
    starts PENDING and is invisible to retrieve_context until an admin
    approves it (or it matches an auto-approve rule) - this is a
    deliberate review gate, not a bug. `title` is a short, searchable
    summary (this is what future retrieve_context anchors match against);
    `body` is the actual content; `tags` and `links` are optional and
    help retrieval and review."""
    _, _, team_id = _config()
    return _request(
        "POST", f"/teams/{team_id}/memory",
        body={"title": title, "body": body, "tags": tags or [], "links": links or []},
    )


if __name__ == "__main__":
    mcp.run()
