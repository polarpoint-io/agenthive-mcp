# agenthive-mcp

An [MCP](https://modelcontextprotocol.io) server that exposes
[AgentHive](https://github.com/polarpoint-io/agenthive)'s two agent-facing
calls - `retrieve_context` and `log_session` - as native tools for Cursor
and Claude Code, instead of needing a `CLAUDE.md` / `.cursor/rules`
instruction block that calls AgentHive's HTTP API by hand.

This repo doesn't run AgentHive itself, and doesn't depend on that repo's
code - it's a thin local wrapper: your IDE spawns `mcp_server.py` as a
subprocess on your own machine, it makes plain HTTP calls to whatever
`AGENTHIVE_URL` you already have running, and it holds no state of its
own. It can do exactly what your `AGENTHIVE_TOKEN` already lets you do
over the API directly - the member/admin split and the pending-approval
review gate are both enforced entirely server-side by AgentHive itself.

## Setup

You need an AgentHive team and a personal token first - see
[AgentHive's onboarding guide](https://github.com/polarpoint-io/agenthive/blob/main/ONBOARDING.md)
if you don't have one yet.

```bash
pip install -r requirements.txt
```

Then add this server to your IDE's MCP config. Claude Code (`.mcp.json`
in your project root, or `claude mcp add`):

```json
{
  "mcpServers": {
    "agenthive": {
      "command": "python3",
      "args": ["/absolute/path/to/agenthive-mcp/mcp_server.py"],
      "env": {
        "AGENTHIVE_URL": "https://your-agenthive-host",
        "AGENTHIVE_TOKEN": "<your personal token>",
        "AGENTHIVE_TEAM_ID": "<team id>"
      }
    }
  }
}
```

Cursor (`.cursor/mcp.json`) uses the identical shape.

Use your own token, not a shared or admin one - whatever it can do
(`member` or `admin`) is what this server can do on your behalf, nothing
more or less. Once your IDE picks up the config, `retrieve_context` and
`log_session` show up as tools the agent can call directly - but tell it
*when* to use them too (start and end of a session), since having the
tools available doesn't make an agent call them unprompted. A short
`CLAUDE.md` / `.cursor/rules` line is enough:

```
Before starting work, call retrieve_context with a short description of
the task. When you're done, call log_session summarizing what you did and
learned.
```

## Tools

- **`retrieve_context(anchor, hops=2, hub_cutoff=15)`** - a bounded
  neighborhood of this team's reviewed, approved memory around a topic.
  Returns `neighborhood` (the matched nodes) and `approx_tokens` (what
  folding them into context actually costs).
- **`log_session(title, body, tags=None, links=None)`** - logs what
  happened this session as a memory node. Starts `pending` and is
  invisible to `retrieve_context` until an admin approves it (or it
  matches an auto-approve rule) - this is a deliberate review gate, not a
  bug. See AgentHive's `ADR.md` for why.

## Testing

```bash
pip install -r requirements-dev.txt
pytest
```

The suite drives `mcp_server.py` as a real subprocess over real MCP
stdio, using a real `mcp.ClientSession` - nothing mocked at the protocol
layer. The AgentHive side is a small local fake
(`tests/fake_agenthive.py`), not the real service, since this repo
shouldn't need AgentHive itself checked out to test its own HTTP
plumbing; AgentHive's own approval-gate/retrieval/auth behavior is
already covered by its own test suite, in its own repo, against the real
`server.py`. See that file's docstring for the reasoning, and try it
against a real AgentHive instance manually if you want to confirm the
whole chain end to end.

## Why a separate repo from AgentHive

This started as a module inside the AgentHive repo itself, then moved
out: it has its own dependency (`mcp`), its own release cadence (an IDE
integration changes on its own schedule, unrelated to the service's), and
no reason to need AgentHive's own container image, Helm chart, or CI
matrix rebuilt every time it changes - or vice versa.
