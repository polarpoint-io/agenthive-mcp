"""Lazy-initialised AgentHive HTTP client.

Reads AGENTHIVE_URL/AGENTHIVE_TOKEN/AGENTHIVE_TEAM_ID from the environment
at call time (not import time), so importing this module never fails just
because env vars are unset - useful for tests, linting, and Docker image
builds, same reasoning as snyk-mcp's client.py.

Deliberately plain urllib rather than an SDK or httpx dependency: AgentHive
is a small first-party HTTP API (see polarpoint-io/agenthive's server.py),
not a large third-party surface worth a client library, and this repo's
whole point is staying a thin, dependency-light bridge (see ADR.md's "Why
not build an LLM-request proxy" in the main agenthive repo).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request


class AgentHiveConfigError(RuntimeError):
    """Raised when required AGENTHIVE_* env vars are missing."""


class AgentHiveError(RuntimeError):
    """Raised on any non-2xx response from the AgentHive API, with the
    server's own error message rather than a generic HTTP status."""


def get_config() -> tuple[str, str, str]:
    """Return (base_url, token, team_id) from the environment, or raise
    AgentHiveConfigError naming exactly what's missing."""
    missing = [
        name for name in ("AGENTHIVE_URL", "AGENTHIVE_TOKEN", "AGENTHIVE_TEAM_ID")
        if not os.environ.get(name)
    ]
    if missing:
        raise AgentHiveConfigError(
            "agenthive-mcp is missing " + ", ".join(missing) + " - set these in "
            "the MCP client config's env block (see README.md)."
        )
    return (
        os.environ["AGENTHIVE_URL"].rstrip("/"),
        os.environ["AGENTHIVE_TOKEN"],
        os.environ["AGENTHIVE_TEAM_ID"],
    )


def request(method: str, path: str, body: dict | None = None, query: dict | None = None) -> dict:
    """A single AgentHive API call using the caller's personal token.
    Every MCP tool in server.py is a thin pass-through to this."""
    base_url, token, _ = get_config()
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
