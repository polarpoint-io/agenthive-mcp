"""
`agenthive` - see and change what the plugin is doing, from a terminal.

A terminal command rather than a slash command on purpose: inside an
agent, asking costs a model turn. Here it costs nothing.
"""

from __future__ import annotations

import json
import os
import sys

from . import facts, manifests, toolprune
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


def cmd_install(argv) -> int:
    """Write this harness's hook manifest, merging rather than replacing.

    Defaults to the harness AGENTHIVE_HOST names, because somebody on
    Cursor has to set that variable anyway and making them repeat it
    here would be the second chance to get it wrong.
    """
    harness, argv = _harness_arg(argv)
    dry = "--dry-run" in argv
    if "--print" in argv:
        sys.stdout.write(manifests.render(harness))
        return 0

    dest = manifests.destination(harness, _project_arg(argv))
    existing = _read_json(dest)
    merged, changed = manifests.merge(existing, harness)

    if not changed:
        print(f"already installed for {harness}: {dest}")
        return 0
    if dry:
        print(f"would write {dest}  (events: {', '.join(changed)})")
        sys.stdout.write(json.dumps(merged, indent=2) + "\n")
        return 0

    if existing is not None:
        backup = dest.with_suffix(dest.suffix + ".agenthive-bak")
        backup.write_text(json.dumps(existing, indent=2) + "\n")
        print(f"backed up existing config to {backup}")

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(merged, indent=2) + "\n")
    print(f"installed for {harness}: {dest}")
    print(f"  events: {', '.join(changed)}")
    if harness == "cursor":
        print("  export AGENTHIVE_HOST=cursor in your shell profile - "
              "without it, session end is a silent no-op")
    print("  the hook does NOT inherit your MCP client's env block; "
          "set AGENTHIVE_URL / AGENTHIVE_TEAM_ID / AGENTHIVE_TOKEN in "
          "your shell profile, then run: agenthive doctor")
    return 0


def cmd_uninstall(argv) -> int:
    """Take back exactly what install wrote, and nothing else."""
    harness, argv = _harness_arg(argv)
    dest = manifests.destination(harness, _project_arg(argv))
    existing = _read_json(dest)
    if existing is None:
        print(f"nothing to remove: {dest} does not exist")
        return 0
    merged, changed = manifests.uninstall(existing, harness)
    if not changed:
        print(f"nothing of ours in {dest}")
        return 0
    dest.write_text(json.dumps(merged, indent=2) + "\n")
    print(f"removed from {dest}  (events: {', '.join(changed)})")
    return 0


def _harness_arg(argv):
    """Pull --harness out of argv, defaulting to AGENTHIVE_HOST."""
    rest = list(argv)
    harness = _host()
    for flag in ("--harness", "-H"):
        if flag in rest:
            i = rest.index(flag)
            try:
                harness = rest[i + 1].strip().lower()
            except IndexError:
                raise SystemExit(f"{flag} needs a value: "
                                 f"{', '.join(sorted(manifests.MANIFESTS))}") from None
            del rest[i:i + 2]
    if harness not in manifests.MANIFESTS:
        raise SystemExit(f"unknown harness {harness!r}; expected one of "
                         f"{', '.join(sorted(manifests.MANIFESTS))}")
    return harness, rest


def _project_arg(argv):
    """VS Code's manifest is per-project, so --project picks which one."""
    if "--project" in argv:
        i = argv.index("--project")
        try:
            return argv[i + 1]
        except IndexError:
            raise SystemExit("--project needs a directory") from None
    return None


def _read_json(path):
    """Existing config, or None. A file we cannot parse is not a None."""
    if not path.exists():
        return None
    text = path.read_text()
    if not text.strip():
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise SystemExit(
            f"{path} is not valid JSON ({e}); refusing to touch it - "
            "fix or move it and run this again"
        ) from None


COMMANDS = {"status": cmd_status, "facts": cmd_facts,
            "toolprune": cmd_toolprune, "doctor": cmd_doctor,
            "install": cmd_install, "uninstall": cmd_uninstall}


def main(argv=None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    if not argv or argv[0] in ("-h", "--help"):
        print("agenthive <install|uninstall|status|facts|toolprune [apply|restore]|doctor>\n"
              "  install [--harness claude|vscode|cursor] [--project DIR] [--dry-run] [--print]")
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
