# CLAUDE.md — agenthive-mcp

## Project overview

`agenthive-mcp` is an MCP (Model Context Protocol) server that exposes [AgentHive](https://github.com/polarpoint-io/agenthive)'s shared, reviewed team memory to LLM clients. It follows the same structure as [snyk-mcp](https://github.com/polarpoint-io/snyk-mcp) and [holmesgpt-runbook-mcp](https://github.com/polarpoint-io/holmesgpt-runbook-mcp).

## Key files

| File | Purpose |
|---|---|
| `src/agenthive_mcp/server.py` | All MCP tools (FastMCP) + entrypoint |
| `src/agenthive_mcp/client.py` | AgentHive HTTP client (env-driven, lazy) |
| `tests/test_tools.py` | Real-stdio-subprocess integration tests |
| `tests/fake_agenthive.py` | Small local fake of AgentHive's HTTP API |
| `pyproject.toml` | Build config and dependencies |
| `Dockerfile` | Container image |
| `Makefile` | Developer shortcuts |

## Architecture

- **Transport**: stdio (default) or SSE/HTTP, controlled by `TRANSPORT` env var
- **Auth**: `AGENTHIVE_TOKEN` - the caller's own personal AgentHive token, member or admin scoped
- **Client**: plain `urllib` calls in `client.py`, no SDK dependency
- **Scope**: only member-role AgentHive endpoints are exposed as tools (`retrieve_context`, `log_session`, `create_agent`, `list_agents`, `create_task`, `list_tasks`) - admin-only endpoints (the review gate, user management, auto-approve rules) are deliberately not tools here

## Common commands

```bash
make dev        # install with dev extras
make test       # run pytest
make lint       # ruff check
make run        # start stdio server
make run-http   # start HTTP/SSE server on :8000
```

## Adding a new tool

1. Add a `@mcp.tool()` function in `server.py`, only for a member-role AgentHive endpoint
2. Call `_request()` from `client.py`
3. Add a matching route to `tests/fake_agenthive.py` if needed
4. Add a test in `tests/test_tools.py` using the real `stdio_client`/`ClientSession` pattern

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `AGENTHIVE_URL` | Yes | Base URL of a running AgentHive service |
| `AGENTHIVE_TOKEN` | Yes | Your personal AgentHive token |
| `AGENTHIVE_TEAM_ID` | Yes | Your AgentHive team id |
| `TRANSPORT` | No | `stdio` or `http` |
| `HTTP_HOST` | No | Bind host for HTTP mode |
| `HTTP_PORT` | No | Bind port for HTTP mode |
| `LOG_LEVEL` | No | Python log level |

## Hooking a *consuming* repo up to AgentHive via this server

This is a note for anyone using `agenthive-mcp` in another repo, not for contributors here - kept in this file because it's the kind of thing an agent working in that other repo needs to know.

Tools existing in the tool list doesn't make an agent call them unprompted - it needs a line telling it when. If that repo uses polarpoint-io's platform-standards `AGENTS.md` template (see [`ai-capabilities/platform-standards/templates/default-agents-md.md`](https://github.com/polarpoint-io/ai-capabilities/blob/main/platform-standards/templates/default-agents-md.md)), the right place is **Zone 3** ("Repo-specific notes" - free, team-owned, explicitly meant for "context about the domain, gotchas, preferred libraries, links to runbooks"):

```markdown
## AgentHive

Before starting work, call `retrieve_context` with a short description of
the task. When you're done, call `log_session` summarizing what you did
and learned.
```

Team-specific AgentHive conventions (e.g. which tags to use, the team's `AGENTHIVE_TEAM_ID`) belong in Zone 2 instead - guarded, but team-owned, and it's meant to extend Zone 1 rather than override it. Don't put either in Zone 1 - that's platform-locked and changing it org-wide is a bigger call than one repo should make; it would need a PR to `ai-capabilities` and platform-team review. Repos without a platform-standards `AGENTS.md` yet can use a plain `CLAUDE.md` / `.cursor/rules` line instead - see `README.md`.
