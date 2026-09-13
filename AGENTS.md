# AGENTS.md — Guidance for AI coding agents

This document provides guidance for AI agents (Claude Code, Cursor, Copilot, etc.) working on the `agenthive-mcp` codebase itself - not for agents *using* it as a tool. If you're looking for how to wire an agent up to AgentHive via this server, see [README.md](README.md); if you're looking for where to put the "call retrieve_context/log_session" instruction in a *consuming* repo, see the "AGENTS.md Zone 3" note in this file's own README.

## Project structure

```
agenthive-mcp/
├── src/agenthive_mcp/
│   ├── __init__.py         # package version
│   ├── client.py           # AgentHive HTTP client (env-driven, lazy)
│   └── server.py           # all FastMCP tools + main() entrypoint
├── tests/
│   ├── fake_agenthive.py   # small local fake of AgentHive's HTTP API
│   └── test_tools.py       # real-stdio-subprocess integration tests
├── pyproject.toml          # build config
├── Dockerfile              # container image
├── Makefile                # dev shortcuts
├── README.md                # user-facing docs
├── CLAUDE.md                # developer context
└── AGENTS.md                # this file
```

## Key design decisions

1. **Self-contained, no dependency on the `agenthive` repo.** This server makes its own plain HTTP calls; it doesn't import `agenthive`'s `client.py`. See `README.md`'s "Why a separate repo" section.
2. **Only member-role calls are exposed.** `list_pending`/`approve`/`reject`, user management, and auto-approve rules are deliberately not tools here - see the module docstring in `server.py` and AgentHive's own `ADR.md` for why.
3. **No state, no decisions.** Every tool is a thin pass-through to AgentHive's HTTP API using the caller's own `AGENTHIVE_TOKEN`. Don't add caching, retries-with-backoff, or client-side validation beyond what's needed for a clear error message - that's AgentHive's job, not this wrapper's.
4. **Real stdio integration tests, not mocked-function unit tests.** Unlike most of the org's other MCP servers (e.g. `snyk-mcp`), `tests/test_tools.py` drives the server as a real subprocess over real MCP stdio against a small local fake of AgentHive's HTTP API (`fake_agenthive.py`). This is deliberate: it already caught a real bug once (FastMCP's `structuredContent` is `None` for a plain `-> dict` return type) that a mocked-function-call test would never have exercised. Keep this pattern for new tools.
5. **Dual transport (stdio default, HTTP/SSE optional).** Controlled by `TRANSPORT` env var, matching the rest of the org's MCP servers. stdio is what almost everyone should use (spawned per-IDE-session, using that developer's own token); HTTP is for the rare case of a shared local/network instance - it's still bound to whatever token the process was started with, it doesn't change the auth model.

## Adding a tool

1. Add a `@mcp.tool()` function in `src/agenthive_mcp/server.py`, calling `_request()` from `client.py`.
2. Only add it if it's a **member**-role AgentHive endpoint - check AgentHive's `server.py` route table first. Admin-only endpoints stay out (see decision #2 above).
3. Add a matching route to `tests/fake_agenthive.py` if the endpoint isn't already covered.
4. Add a test in `tests/test_tools.py` following the existing `stdio_client`/`ClientSession` pattern.
5. Update the "Tools" table in `README.md`.

## Linting

```bash
make lint     # ruff check src tests
make format   # ruff check --fix src tests
```

## Testing

```bash
make test     # pytest -q
```

Tests must pass before any PR is merged. Do not mark a task complete if tests are failing.

## Release process

Fully automated via semantic-release on every push to `main` - see `README.md`'s "Publishing" section. Use [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `feat!:` for breaking changes) so the version bump and changelog are correct; `chore:`/`docs:`/`ci:` don't trigger a release.
