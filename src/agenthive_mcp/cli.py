"""
`agenthive` - see and change what the plugin is doing, from a terminal.

A terminal command rather than a slash command on purpose: inside an
agent, asking costs a model turn. Here it costs nothing.
"""

from __future__ import annotations

import os
import sys

from . import facts, toolprune
from .client import AgentHiveConfigError, get_config


def _host() -> str:
    return (os.environ.get("AGENTHIVE_HOST") or "claude").strip().lower()


def cmd_status(argv) -> int:
    try:
        url, _, team = get_config()
    except AgentHiveConfigError as e:
        print(f"not configured: {e}")
        return 1
    payload = facts.fetch(os.getcwd())
    prune = toolprune.proposal()
    print(f"  server      {url}")
    print(f"  team        {team}")
    print(f"  host        {_host()}")
    print(f"  facts       {len(payload.get('facts') or [])} "
          f"(~{payload.get('approx_tokens', 0)} tokens injected per session)")
    print(f"  tool prune  {len(prune.get('prune') or [])} proposed, "
          f"{len(prune.get('not_yet') or [])} awaiting evidence")
    for name in ("facts", "toolprune", "loopguard"):
        flag = os.environ.get(f"AGENTHIVE_{name.upper()}", "on")
        print(f"  {name:<11} {flag}")
    return 0


def cmd_facts(argv) -> int:
    payload = facts.fetch(os.getcwd())
    rendered = facts.render(payload)
    if not rendered:
        print("No facts yet. Log one with the env-fact tag - short, "
              "e.g. 'tests: python -m pytest -q'.")
        return 0
    print(rendered)
    print(f"\n~{payload.get('approx_tokens', 0)} tokens, injected at every "
          "session start for this team.")
    return 0


def cmd_toolprune(argv) -> int:
    if argv and argv[0] == "restore":
        ok, why = toolprune.restore(_host(), os.getcwd())
        print(why)
        return 0 if ok else 1

    proposal = toolprune.proposal()
    for tool in proposal.get("prune") or []:
        print(f"  prune    {tool['tool']:<28} unused across "
              f"{tool['sessions']} sessions, {tool['users']} people")
    for tool in proposal.get("not_yet") or []:
        print(f"  not yet  {tool['tool']:<28} {tool.get('needs', '')}")
    if not (proposal.get("prune") or proposal.get("not_yet")):
        print("No tool evidence yet. It accumulates as sessions run.")
        return 0

    if argv and argv[0] == "apply":
        ok, why = toolprune.apply(_host(), os.getcwd(),
                                  [t["tool"] for t in proposal.get("prune") or []])
        print(f"\n{why}")
        return 0 if ok else 1
    print("\nNothing changed. `agenthive toolprune apply` to write the deny list.")
    return 0


def cmd_doctor(argv) -> int:
    print(f"  host detected   {_host()}")
    try:
        url, _, team = get_config()
        print(f"  config          ok ({url}, team {team})")
    except AgentHiveConfigError as e:
        print(f"  config          MISSING - {e}")
        print("\n  The hook does NOT inherit your MCP client's env block.")
        print("  See the README section 'Giving the hook its credentials'.")
        return 1
    payload = facts.fetch(os.getcwd())
    print(f"  server          reachable, {len(payload.get('facts') or [])} facts")
    return 0


COMMANDS = {"status": cmd_status, "facts": cmd_facts,
            "toolprune": cmd_toolprune, "doctor": cmd_doctor}


def main(argv=None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    if not argv or argv[0] in ("-h", "--help"):
        print("agenthive <status|facts|toolprune [apply|restore]|doctor>")
        return 0
    cmd = COMMANDS.get(argv[0])
    if not cmd:
        print(f"unknown command {argv[0]!r}; try: {', '.join(sorted(COMMANDS))}")
        return 2
    try:
        return cmd(argv[1:])
    except Exception as e:  # noqa: BLE001
        print(f"error: {e!r}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
