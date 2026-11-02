# The AgentHive plugin

Three harnesses, one implementation. The hook logic lives in
`src/agenthive_mcp/` and these directories are thin manifests pointing
at the `agenthive-hook` entry point, so nothing is duplicated per IDE.

| | Hook format | Manifest goes to |
|---|---|---|
| Claude Code | native | `~/.claude/settings.json` |
| VS Code | same format as Claude Code | `.github/hooks/agenthive.json` |
| Cursor | its own events | `~/.cursor/hooks.json` |

VS Code uses the same hook format as Claude Code, so both read the same
payload and the same handlers serve them. Cursor's event names differ
(`beforeSubmitPrompt`, `afterFileEdit`, `stop`) and are translated in
`hooks.py` rather than duplicated downstream.

## Install

```sh
pip install agenthive-mcp
```

Then copy the manifest for your harness, and give the hook its
credentials — **it does not inherit your MCP client's `env` block**, see
the main README.

**Claude Code**
```sh
mkdir -p ~/.claude && cp plugin/claude/settings.json ~/.claude/settings.json
```

**VS Code**
```sh
mkdir -p .github/hooks && cp plugin/vscode/agenthive.json .github/hooks/
```

**Cursor**
```sh
mkdir -p ~/.cursor && cp plugin/cursor/hooks.json ~/.cursor/
export AGENTHIVE_HOST=cursor
```

Check it with `agenthive doctor`.

`AGENTHIVE_HOST` matters on Cursor. `stop` exists in both hook families
and carries no marker saying which sent it, so the sniff can guess
wrong — and a wrong guess turns session-end into a silent no-op.

## What each mechanism does

| | What it does | Switch |
|---|---|---|
| **Facts** | Injects short team facts at session start, with no anchor and no tool call. The one path that replaces exploration rather than adding to context. | `AGENTHIVE_FACTS` |
| **Tool pruning** | Tracks which loaded tools a session uses, pools it across the team, writes a deny list once several people agree. | `AGENTHIVE_TOOLPRUNE` |
| **Loop guard** | Notices a turn repeating the same failure and adds one note suggesting another approach. | `AGENTHIVE_LOOPGUARD` |

All three default on. Set any to `off`.

## What it will not do

**It never stops a turn.** The loop guard says what it sees and how to
stop it; interrupting somebody's agent on a heuristic is not its call.

**It never calls a model.** The loop guard counts repeated failures
locally. AgentHive the service stays model-free, and so does this — the
rule was not relocated, it was kept.

**It never breaks a session.** Every handler is wrapped and the entry
point always exits 0. A bug in fact injection cannot stop a session that
was about to work.

**It only removes its own tool rules.** `agenthive toolprune restore`
takes back exactly what the plugin wrote and leaves any rule you added.

## Caveats worth knowing

- **VS Code hooks are in preview.** The format may change; expect to
  chase it.
- **Policy can disable hooks or MCP** on Copilot Business and
  Enterprise. The plugin degrades to MCP-only and says so.
- **Tool pruning differs per harness** — it writes each one's own
  settings, and what can be removed varies. On Cursor and VS Code this
  may do nothing yet.
- **None of this has been tested against a live IDE.** The logic is
  unit-tested; the contact with each harness is not.
