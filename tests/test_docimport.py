"""docimport.py (pure filesystem/string logic) and cli.py's import-docs
command (the only thing that calls AgentHive, and only with --apply)."""
import os
import sys

import pytest
from fake_agenthive import FakeAgentHive

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
sys.path.insert(0, SRC)

from agenthive_mcp import (
    cli,
    docimport,
)

TOKEN = "test-token-abc123"
TEAM_ID = "team-xyz"


@pytest.fixture()
def fake_server():
    server = FakeAgentHive(token=TOKEN, team_id=TEAM_ID)
    try:
        yield server
    finally:
        server.close()


@pytest.fixture()
def env(fake_server, monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTHIVE_URL", fake_server.base_url)
    monkeypatch.setenv("AGENTHIVE_TOKEN", TOKEN)
    monkeypatch.setenv("AGENTHIVE_TEAM_ID", TEAM_ID)
    # Isolate the local dedup state (shared with hooklog.py's state_dir())
    # from the real machine's state and from other tests.
    monkeypatch.setenv("AGENTHIVE_HOOK_STATE_DIR", str(tmp_path / "state"))
    return fake_server


def write(path, name, text):
    full = path / name
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(text)
    return full


# ---- split_sections -------------------------------------------------

def test_splits_on_headings_of_any_level():
    text = "# Title\nintro line\n## First\nbody one\n### Nested\nbody two\n"
    sections = docimport.split_sections(text, heading_prefix="doc.md")
    assert [h for h, _ in sections] == ["Title", "First", "Nested"]
    assert sections[0][1] == "intro line"
    assert sections[1][1] == "body one"
    assert sections[2][1] == "body two"


def test_content_before_first_heading_keeps_the_file_as_its_title():
    text = "no heading here, just prose\nmore prose\n"
    sections = docimport.split_sections(text, heading_prefix="README.md")
    assert sections == [("README.md", "no heading here, just prose\nmore prose")]


def test_a_bare_heading_with_nothing_under_it_is_dropped():
    text = "## Empty\n## Has content\nactual text\n"
    sections = docimport.split_sections(text, heading_prefix="x.md")
    assert sections == [("Has content", "actual text")]


# ---- discover_markdown_files -----------------------------------------

def test_finds_markdown_at_any_depth(tmp_path):
    write(tmp_path, "README.md", "root doc")
    write(tmp_path, "docs/runbook.md", "nested doc")
    write(tmp_path, "docs/notes.markdown", "nested doc, other extension")
    write(tmp_path, "src/app.py", "print('not markdown')")

    found = docimport.discover_markdown_files(str(tmp_path))
    names = sorted(p.name for p in found)
    assert names == ["README.md", "notes.markdown", "runbook.md"]


def test_skips_vcs_and_dependency_directories(tmp_path):
    write(tmp_path, "README.md", "keep me")
    write(tmp_path, ".git/COMMIT_EDITMSG.md", "not a real doc")
    write(tmp_path, "node_modules/some-pkg/README.md", "dependency noise")
    write(tmp_path, "vendor/lib/README.md", "vendored noise")

    found = docimport.discover_markdown_files(str(tmp_path))
    assert [p.name for p in found] == ["README.md"]


# ---- sections_for_file / plan -----------------------------------------

def test_sections_below_min_chars_are_dropped(tmp_path):
    write(tmp_path, "notes.md", "## Tiny\nhi\n## Real section\n" + "x" * 50)
    sections = docimport.plan(str(tmp_path), min_chars=40)
    assert len(sections) == 1
    assert sections[0].title == "notes.md — Real section"
    assert sections[0].source == "notes.md"


def test_plan_is_pure_and_touches_no_network(tmp_path):
    write(tmp_path, "a.md", "# A\n" + "content " * 10)
    # No AGENTHIVE_* env vars set at all; plan() must not need them.
    sections = docimport.plan(str(tmp_path))
    assert len(sections) == 1


# ---- cli import-docs: dry run ------------------------------------------

def test_dry_run_prints_the_plan_and_writes_nothing(tmp_path, env, capsys):
    write(tmp_path, "runbook.md", "## Deploying\n" + "steps go here " * 5)
    rc = cli.main(["import-docs", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "would be created" in out
    assert "Nothing written" in out
    assert env.nodes == {}


def test_dry_run_needs_no_agenthive_config(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("AGENTHIVE_URL", raising=False)
    monkeypatch.delenv("AGENTHIVE_TOKEN", raising=False)
    monkeypatch.delenv("AGENTHIVE_TEAM_ID", raising=False)
    write(tmp_path, "runbook.md", "## Deploying\n" + "steps go here " * 5)
    rc = cli.main(["import-docs", str(tmp_path)])
    assert rc == 0


# ---- cli import-docs: --apply ------------------------------------------

def test_apply_logs_each_section_as_a_pending_node(tmp_path, env, capsys):
    write(tmp_path, "runbook.md",
          "## Deploying\n" + "steps go here " * 5 +
          "\n## Rolling back\n" + "rollback steps " * 5)
    rc = cli.main(["import-docs", str(tmp_path), "--apply"])
    out = capsys.readouterr().out

    assert rc == 0
    assert len(env.nodes) == 2
    titles = {n["title"] for n in env.nodes.values()}
    assert titles == {"runbook.md — Deploying", "runbook.md — Rolling back"}
    for node in env.nodes.values():
        assert node["status"] == "pending"
        assert "doc-import" in node["tags"]
        assert node["links"] == ["runbook.md"]
    assert "2 node(s) logged as pending" in out


def test_apply_without_config_fails_before_writing_anything(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("AGENTHIVE_URL", raising=False)
    monkeypatch.delenv("AGENTHIVE_TOKEN", raising=False)
    monkeypatch.delenv("AGENTHIVE_TEAM_ID", raising=False)
    write(tmp_path, "runbook.md", "## Deploying\n" + "steps go here " * 5)
    rc = cli.main(["import-docs", str(tmp_path), "--apply"])
    assert rc == 1
    assert "not configured" in capsys.readouterr().out


def test_custom_tags_replace_the_default_doc_import_tag_additively(tmp_path, env):
    write(tmp_path, "runbook.md", "## Deploying\n" + "steps go here " * 5)
    cli.main(["import-docs", str(tmp_path), "--tag", "runbooks", "--apply"])
    (node,) = env.nodes.values()
    assert node["tags"] == ["doc-import", "runbooks"]


def test_limit_caps_how_many_nodes_one_run_creates(tmp_path, env):
    write(tmp_path, "a.md", "## One\n" + "x" * 50)
    write(tmp_path, "b.md", "## Two\n" + "x" * 50)
    write(tmp_path, "c.md", "## Three\n" + "x" * 50)
    cli.main(["import-docs", str(tmp_path), "--limit", "2", "--apply"])
    assert len(env.nodes) == 2


def test_no_importable_sections_is_not_an_error(tmp_path, env, capsys):
    write(tmp_path, "empty.md", "hi")  # under the 40-char default
    rc = cli.main(["import-docs", str(tmp_path)])
    assert rc == 0
    assert "no importable sections" in capsys.readouterr().out
    assert env.nodes == {}


# ---- local dedup: safe to re-run, including unreviewed ----------------

def test_a_second_apply_skips_what_the_first_already_submitted(tmp_path, env, capsys):
    write(tmp_path, "runbook.md", "## Deploying\n" + "steps go here " * 5)

    cli.main(["import-docs", str(tmp_path), "--apply"])
    assert len(env.nodes) == 1

    rc = cli.main(["import-docs", str(tmp_path), "--apply"])
    out = capsys.readouterr().out
    assert rc == 0
    assert len(env.nodes) == 1  # not 2 - nothing new was created
    assert "nothing new to import" in out
    assert "1 already submitted" in out


def test_skip_holds_even_though_the_first_nodes_are_still_pending(tmp_path, env):
    write(tmp_path, "runbook.md", "## Deploying\n" + "steps go here " * 5)
    cli.main(["import-docs", str(tmp_path), "--apply"])
    (node,) = env.nodes.values()
    assert node["status"] == "pending"  # nobody reviewed it

    cli.main(["import-docs", str(tmp_path), "--apply"])
    assert len(env.nodes) == 1  # still skipped - dedup doesn't wait for approval


def test_editing_a_section_resubmits_it_despite_the_unchanged_title(tmp_path, env):
    write(tmp_path, "runbook.md", "## Deploying\n" + "old steps " * 5)
    cli.main(["import-docs", str(tmp_path), "--apply"])

    write(tmp_path, "runbook.md", "## Deploying\n" + "new steps, revised " * 5)
    cli.main(["import-docs", str(tmp_path), "--apply"])

    assert len(env.nodes) == 2
    bodies = {n["body"] for n in env.nodes.values()}
    assert any("new steps" in b for b in bodies)


def test_force_resubmits_even_an_unchanged_already_submitted_section(tmp_path, env):
    write(tmp_path, "runbook.md", "## Deploying\n" + "steps go here " * 5)
    cli.main(["import-docs", str(tmp_path), "--apply"])
    cli.main(["import-docs", str(tmp_path), "--force", "--apply"])
    assert len(env.nodes) == 2


def test_dry_run_reports_how_many_were_already_submitted(tmp_path, env, capsys):
    write(tmp_path, "runbook.md", "## Deploying\n" + "steps go here " * 5)
    cli.main(["import-docs", str(tmp_path), "--apply"])
    capsys.readouterr()

    rc = cli.main(["import-docs", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "0 node(s) would be created" in out
    assert "1 already submitted by an earlier run" in out


def test_a_failed_post_is_not_marked_submitted_so_it_can_retry(tmp_path, env):
    write(tmp_path, "runbook.md", "## Deploying\n" + "steps go here " * 5)

    from agenthive_mcp.client import AgentHiveError

    original_request = cli._request

    def _boom(*a, **k):
        raise AgentHiveError("500: simulated outage")

    cli._request = _boom
    try:
        cli.main(["import-docs", str(tmp_path), "--apply"])
    finally:
        cli._request = original_request

    # Real client restored; the earlier failure must not have been marked
    # as submitted, so this retry actually creates the node.
    cli.main(["import-docs", str(tmp_path), "--apply"])
    assert len(env.nodes) == 1
