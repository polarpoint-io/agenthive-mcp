"""
`agenthive` - see and change what the plugin is doing, from a terminal.

A terminal command rather than a slash command on purpose: inside an
agent, asking costs a model turn. Here it costs nothing.
"""

from __future__ import annotations

import json
import os
import sys

from . import docimport, facts, manifests, toolprune
from .client import AgentHiveConfigError, AgentHiveError, get_config
from .client import request as _request


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
    for name in ("facts", "codemap", "toolprune", "loopguard"):
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


def cmd_import_docs(argv) -> int:
    """Turn a repo of markdown docs into AgentHive memory nodes.

    Dry run by default - this is a command a human runs once or
    occasionally, not something an agent decides to do mid-session (see
    docimport.py's docstring), and the review queue is the thing a bulk
    import can genuinely flood. `--apply` is the one flag that actually
    writes anything.

    Safe to re-run by hand: a (team, title, body) already submitted by
    a previous --apply is skipped even if nobody has reviewed it yet
    (see docimport.py's "WHY THIS MODULE HAS ITS OWN DEDUP"). `--force`
    bypasses that local check for one run.

    On CI, prefer `--since REF` over relying on that: it scopes to
    only the markdown git shows changed since REF, which needs no
    state to persist between runs - the runner's own checkout already
    has it. The local dedup above does NOT survive an ephemeral
    runner unless its state dir is explicitly cached, so without
    `--since` a fresh runner re-walks and resubmits the whole tree
    every time.

    PATH can be a git URL (https://, ssh://, git@host:path, or anything
    ending .git) instead of a local directory - it's cloned to a
    temporary directory for the run and removed afterward. With
    `--since`, the clone fetches full history (REF has to actually be
    in it to diff against); without it, a shallow depth-1 clone, since
    a whole-tree plan only needs the current snapshot.

    `--only-tag` scopes to files whose own frontmatter `tags:` include
    at least one of these (a docs site's mkdocs tags - `runbook`, `kb`,
    `adr` - are often the real classification `--exclude`'s path globs
    can only approximate). Every imported node carries its file's
    frontmatter tags regardless, so they reach AgentHive as real tags
    whether or not you ever filter on them.

    `--limit` caps how many *new* nodes one run creates, to fill the
    review queue in batches rather than flooding it - but by itself it
    always re-slices the same leading N of whatever's left, which only
    advances to the next batch because local dedup state marks the
    previous batch as already-submitted on a persistent machine. That
    tracking does NOT survive an ephemeral CI runner (see --since
    above) and isn't shared across people running this from different
    machines, so in either case `--limit` alone would reselect the
    same first batch forever. `--offset N` advances explicitly instead,
    needing no state of its own: skip the first N of what's left, then
    take the next `--limit`.
    """
    if not argv or argv[0] in ("-h", "--help"):
        print("agenthive import-docs PATH [--tag TAG]... [--exclude PATTERN]... "
              "[--only-tag TAG]... [--since REF] [--min-chars N] [--offset N] "
              "[--limit N] [--force] [--apply]\n"
              "  PATH: a local directory, or a git URL to clone for this run.\n"
              "  --exclude: skip files whose path matches this glob, e.g. "
              "'docs/blog/*' (repeatable).\n"
              "  --only-tag: skip files whose frontmatter `tags:` don't "
              "include this (repeatable, OR'd together).\n"
              "  --since: only markdown changed since this git ref "
              "(the CI-safe mode - see --help text above).\n"
              "  --offset: skip this many of what's left before applying "
              "--limit - the stateless way to advance to the next batch.\n"
              "  Without --apply: prints the nodes this would create and "
              "writes nothing.\n"
              "  --force: also (re-)submit sections already submitted by "
              "an earlier run.")
        return 0

    path = argv[0]
    rest = argv[1:]
    apply = "--apply" in rest
    force = "--force" in rest
    rest = [a for a in rest if a not in ("--apply", "--force")]

    tags = ["doc-import"]
    while "--tag" in rest:
        i = rest.index("--tag")
        try:
            tags.append(rest[i + 1])
        except IndexError:
            raise SystemExit("--tag needs a value") from None
        del rest[i:i + 2]

    exclude = []
    while "--exclude" in rest:
        i = rest.index("--exclude")
        try:
            exclude.append(rest[i + 1])
        except IndexError:
            raise SystemExit("--exclude needs a value") from None
        del rest[i:i + 2]

    only_tags = []
    while "--only-tag" in rest:
        i = rest.index("--only-tag")
        try:
            only_tags.append(rest[i + 1])
        except IndexError:
            raise SystemExit("--only-tag needs a value") from None
        del rest[i:i + 2]

    min_chars = 40
    if "--min-chars" in rest:
        i = rest.index("--min-chars")
        try:
            min_chars = int(rest[i + 1])
        except (IndexError, ValueError):
            raise SystemExit("--min-chars needs an integer") from None
        del rest[i:i + 2]

    limit = None
    if "--limit" in rest:
        i = rest.index("--limit")
        try:
            limit = int(rest[i + 1])
        except (IndexError, ValueError):
            raise SystemExit("--limit needs an integer") from None
        if limit < 0:
            # A negative value is a valid Python slice endpoint (counts
            # from the end), so new_sections[:limit] would silently keep
            # all-but-the-last-N instead of rejecting the batch size -
            # exactly the queue-flooding case --limit exists to prevent.
            raise SystemExit("--limit must be 0 or a positive integer")
        del rest[i:i + 2]

    offset = 0
    if "--offset" in rest:
        i = rest.index("--offset")
        try:
            offset = int(rest[i + 1])
        except (IndexError, ValueError):
            raise SystemExit("--offset needs an integer") from None
        if offset < 0:
            raise SystemExit("--offset must be 0 or a positive integer")
        del rest[i:i + 2]

    since = None
    if "--since" in rest:
        i = rest.index("--since")
        try:
            since = rest[i + 1]
        except IndexError:
            raise SystemExit("--since needs a git ref") from None
        del rest[i:i + 2]

    is_remote = docimport.looks_like_remote(path)
    if not is_remote and not os.path.isdir(path):
        print(f"not a directory: {path}")
        return 1

    try:
        with docimport.checkout(path, shallow=since is None) as root:
            sections = docimport.plan(
                root, min_chars=min_chars, exclude=exclude, since=since,
                only_tags=only_tags or None,
            )
    except RuntimeError as e:
        print(str(e))
        return 1

    if not sections:
        scope = f"changed since {since}" if since else f"under {path}"
        print(f"no importable sections found {scope} "
              f"(nothing ≥ {min_chars} chars)")
        return 0

    # Config is what lets us compute team_scope and so check the local
    # dedup state at all - optional for a dry run (it just can't show
    # what's already been submitted), required for --apply.
    team_scope = None
    config_error = None
    try:
        base_url, _, team_id = get_config()
        team_scope = f"{base_url}|{team_id}"
    except AgentHiveConfigError as e:
        config_error = e

    if team_scope and not force:
        new_sections = [s for s in sections if not docimport.already_submitted(team_scope, s)]
        skipped = len(sections) - len(new_sections)
    else:
        new_sections = sections
        skipped = 0

    if offset:
        new_sections = new_sections[offset:]
    if limit is not None:
        new_sections = new_sections[:limit]

    if not apply:
        for s in new_sections:
            extra = f"  [{', '.join(s.tags)}]" if s.tags else ""
            print(f"  {len(s.body):>6} chars  {s.title}{extra}")
        print(f"\n{len(new_sections)} node(s) would be created, tagged "
              f"{', '.join(tags)} plus each file's own frontmatter tags "
              "(shown above). Each normally starts PENDING until an admin "
              "reviews it, unless your AgentHive's own auto-approve/"
              "auto-reject rules decide it outright - that's server policy, "
              "not something this dry run can predict.")
        if skipped:
            print(f"{skipped} already submitted by an earlier run - "
                  "skipped (would still skip under --apply; --force to "
                  "include them anyway).")
        if team_scope is None:
            print("(set AGENTHIVE_URL/AGENTHIVE_TOKEN/AGENTHIVE_TEAM_ID to "
                  "also see what's already been submitted)")
        print("Nothing written - re-run with --apply.")
        return 0

    if config_error:
        print(f"not configured: {config_error}")
        return 1

    if not new_sections:
        print(f"nothing new to import under {path} - "
              f"{skipped} already submitted. --force to resubmit anyway.")
        return 0

    by_status: dict[str, int] = {}
    failed = 0
    for s in new_sections:
        try:
            node_tags = sorted({*tags, *s.tags})  # + the file's own frontmatter tags
            result = _request(
                "POST", f"/teams/{team_id}/memory",
                body={"title": s.title, "body": s.body, "tags": node_tags,
                      "links": [s.source]},
            )
            # AgentHive's own auto-approve/auto-reject rules can resolve
            # a node on the spot - "pending" is the common case, not a
            # guarantee, so report what the server actually decided
            # rather than assuming every create sat down in the queue.
            status = result.get("status") or "pending"
            by_status[status] = by_status.get(status, 0) + 1
            docimport.mark_submitted(team_scope, s)
            print(f"  {status:<9}{s.title}")
        except AgentHiveError as e:
            # Not marked as submitted - a failed POST should be retried
            # by the next run, not silently skipped forever.
            failed += 1
            print(f"  FAILED   {s.title}: {e}")

    created = sum(by_status.values())
    breakdown = ", ".join(f"{n} {status}" for status, n in sorted(by_status.items()))
    print(f"\n{created} node(s) logged" + (f" ({breakdown})" if breakdown else "") +
          (f", {failed} failed" if failed else "") +
          (f", {skipped} already submitted" if skipped else "") +
          " - a pending one needs an admin's review before it reaches "
          "anyone's retrieve_context; an approved one already has.")
    return 1 if failed else 0


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
            "install": cmd_install, "uninstall": cmd_uninstall,
            "import-docs": cmd_import_docs}


def main(argv=None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    if not argv or argv[0] in ("-h", "--help"):
        print("agenthive <install|uninstall|status|facts|toolprune [apply|restore]|doctor|import-docs>\n"
              "  install [--harness claude|vscode|cursor|copilot] [--project DIR] [--dry-run] [--print]\n"
              "  import-docs PATH [--tag TAG]... [--exclude PATTERN]... [--only-tag TAG]... "
              "[--since REF] [--min-chars N] [--offset N] [--limit N] [--force] [--apply]")
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
