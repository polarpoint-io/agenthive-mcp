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


def test_exclude_glob_matches_across_slashes_without_double_star(tmp_path):
    write(tmp_path, "AGENTS.md", "keep me")
    write(tmp_path, "docs/technical-practices/testing.md", "keep me too")
    write(tmp_path, "docs/blog/posts/2026-launch.md", "public blog noise")
    write(tmp_path, "docs/blog/index.md", "also blog noise")

    found = docimport.discover_markdown_files(str(tmp_path), exclude=["docs/blog/*"])
    rels = {p.relative_to(tmp_path).as_posix() for p in found}
    assert rels == {"AGENTS.md", "docs/technical-practices/testing.md"}


def test_multiple_exclude_patterns_are_all_applied(tmp_path):
    write(tmp_path, "AGENTS.md", "keep me")
    write(tmp_path, "docs/blog/post.md", "blog noise")
    write(tmp_path, "theme/home.md", "theme noise")

    found = docimport.discover_markdown_files(
        str(tmp_path), exclude=["docs/blog/*", "theme/*"])
    assert [p.name for p in found] == ["AGENTS.md"]


def test_no_exclude_patterns_behaves_like_before(tmp_path):
    write(tmp_path, "a.md", "one")
    write(tmp_path, "b.md", "two")
    assert len(docimport.discover_markdown_files(str(tmp_path), exclude=[])) == 2
    assert len(docimport.discover_markdown_files(str(tmp_path))) == 2


# ---- changed_markdown_files / --since (the CI-safe mode) --------------

def _git(repo, *args):
    import subprocess
    subprocess.run(["git", *args], cwd=repo, check=True,
                    capture_output=True, text=True)


def _init_git_repo(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    return tmp_path


def test_since_returns_only_files_changed_after_the_given_ref(tmp_path):
    _init_git_repo(tmp_path)
    write(tmp_path, "unchanged.md", "## Old\n" + "never touched again " * 5)
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "baseline")
    before = _git_rev(tmp_path)

    write(tmp_path, "new.md", "## New\n" + "brand new content " * 5)
    write(tmp_path, "unchanged.md", "## Old\n" + "never touched again " * 5)  # untouched
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "add new.md")

    changed = docimport.changed_markdown_files(str(tmp_path), before)
    assert [p.name for p in changed] == ["new.md"]


def test_since_excludes_deleted_files(tmp_path):
    _init_git_repo(tmp_path)
    write(tmp_path, "keep.md", "## Keep\n" + "sticks around " * 5)
    write(tmp_path, "gone.md", "## Gone\n" + "about to be deleted " * 5)
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "baseline")
    before = _git_rev(tmp_path)

    (tmp_path / "gone.md").unlink()
    write(tmp_path, "keep.md", "## Keep\n" + "edited this time " * 5)
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "edit keep, delete gone")

    changed = docimport.changed_markdown_files(str(tmp_path), before)
    assert [p.name for p in changed] == ["keep.md"]


def test_since_respects_exclude_patterns(tmp_path):
    _init_git_repo(tmp_path)
    _git(tmp_path, "commit", "-q", "--allow-empty", "-m", "baseline")
    before = _git_rev(tmp_path)

    write(tmp_path, "docs/blog/post.md", "## Post\n" + "blog noise " * 5)
    write(tmp_path, "AGENTS.md", "## Standards\n" + "real content " * 5)
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "add two files")

    changed = docimport.changed_markdown_files(str(tmp_path), before, exclude=["docs/blog/*"])
    assert [p.name for p in changed] == ["AGENTS.md"]


def test_since_raises_a_clear_error_on_an_unknown_ref(tmp_path):
    _init_git_repo(tmp_path)
    _git(tmp_path, "commit", "-q", "--allow-empty", "-m", "baseline")
    with pytest.raises(RuntimeError):
        docimport.changed_markdown_files(str(tmp_path), "not-a-real-ref")


def _git_rev(repo):
    import subprocess
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                          capture_output=True, text=True, check=True).stdout.strip()


def test_cli_since_only_creates_nodes_for_what_actually_changed(tmp_path, env):
    _init_git_repo(tmp_path)
    write(tmp_path, "unchanged.md", "## Old\n" + "never touched again " * 5)
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "baseline")
    before = _git_rev(tmp_path)

    write(tmp_path, "new.md", "## New\n" + "brand new content " * 5)
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "add new.md")

    cli.main(["import-docs", str(tmp_path), "--since", before, "--apply"])

    assert len(env.nodes) == 1
    (node,) = env.nodes.values()
    assert node["links"] == ["new.md"]


# ---- frontmatter tags / --only-tag --------------------------------------

def test_split_frontmatter_reads_an_inline_tag_list():
    text = "---\ntitle: KB\ntags: [runbook, kb]\n---\n## Body\ncontent\n"
    tags, body = docimport._split_frontmatter(text)
    assert tags == ["runbook", "kb"]
    assert "---" not in body
    assert "## Body" in body


def test_split_frontmatter_reads_a_bullet_tag_list():
    text = "---\ntags:\n  - runbook\n  - oncall\ndraft: false\n---\n## Body\ncontent\n"
    tags, body = docimport._split_frontmatter(text)
    assert tags == ["runbook", "oncall"]
    assert "draft" not in body


def test_split_frontmatter_with_no_tags_key_is_empty():
    text = "---\ntitle: KB\n---\n## Body\ncontent\n"
    tags, body = docimport._split_frontmatter(text)
    assert tags == []
    assert "## Body" in body


def test_split_frontmatter_with_no_frontmatter_returns_text_unchanged():
    text = "## Body\ncontent\n"
    tags, body = docimport._split_frontmatter(text)
    assert tags == []
    assert body == text


def test_sections_for_file_carries_frontmatter_tags(tmp_path):
    path = write(tmp_path, "kb.md", "---\ntags: [runbook, kb]\n---\n## Steps\n" + "do this thing " * 5)
    sections = docimport.sections_for_file(path, tmp_path, min_chars=10)
    assert sections[0].tags == ["runbook", "kb"]


def test_only_tag_keeps_files_matching_any_of_the_requested_tags(tmp_path):
    write(tmp_path, "runbook.md", "---\ntags: [runbook]\n---\n## Steps\n" + "do this thing " * 5)
    write(tmp_path, "blog.md", "---\ntags: [marketing]\n---\n## Post\n" + "marketing fluff " * 5)
    write(tmp_path, "untagged.md", "## Notes\n" + "no frontmatter at all " * 5)

    sections = docimport.plan(str(tmp_path), only_tags=["runbook"])
    assert [s.source for s in sections] == ["runbook.md"]


def test_without_only_tag_untagged_and_tagged_files_both_import(tmp_path):
    write(tmp_path, "runbook.md", "---\ntags: [runbook]\n---\n## Steps\n" + "do this thing " * 5)
    write(tmp_path, "untagged.md", "## Notes\n" + "no frontmatter at all " * 5)

    sections = docimport.plan(str(tmp_path))
    assert {s.source for s in sections} == {"runbook.md", "untagged.md"}


def test_cli_only_tag_sends_frontmatter_tags_on_the_created_node(tmp_path, env):
    write(tmp_path, "runbook.md", "---\ntags: [runbook, kb]\n---\n## Steps\n" + "do this thing " * 5)
    write(tmp_path, "blog.md", "---\ntags: [marketing]\n---\n## Post\n" + "marketing fluff " * 5)

    cli.main(["import-docs", str(tmp_path), "--only-tag", "runbook", "--apply"])

    assert len(env.nodes) == 1
    (node,) = env.nodes.values()
    assert node["links"] == ["runbook.md"]
    assert set(node["tags"]) == {"doc-import", "runbook", "kb"}


# ---- checkout() / remote repos -----------------------------------------

def test_looks_like_remote_matches_common_git_url_shapes():
    for url in (
        "https://github.com/polarpoint-io/markdown-pol-docs",
        "https://github.com/polarpoint-io/markdown-pol-docs.git",
        "git@github.com:polarpoint-io/markdown-pol-docs.git",
        "ssh://git@github.com/polarpoint-io/markdown-pol-docs.git",
    ):
        assert docimport.looks_like_remote(url), url


def test_looks_like_remote_leaves_local_paths_alone():
    for path in ("/home/claude/markdown-pol-docs", "./docs", "docs", "../other-repo"):
        assert not docimport.looks_like_remote(path), path


def test_checkout_yields_a_plain_path_unchanged(tmp_path):
    with docimport.checkout(str(tmp_path)) as root:
        assert root == str(tmp_path)


def test_checkout_clones_a_remote_url_and_cleans_up_after(tmp_path):
    origin = tmp_path / "origin"
    origin.mkdir()
    _init_git_repo(origin)
    write(origin, "README.md", "## Hello\n" + "real content " * 5)
    _git(origin, "add", "-A")
    _git(origin, "commit", "-q", "-m", "baseline")

    captured = None
    with docimport.checkout(f"file://{origin}") as root:
        captured = root
        assert root != str(origin)  # a real clone, not the original path
        assert os.path.exists(os.path.join(root, "README.md"))

    assert not os.path.exists(captured)  # removed once the block exits


def test_checkout_shallow_clone_cannot_diff_against_an_earlier_ref(tmp_path):
    origin = tmp_path / "origin"
    origin.mkdir()
    _init_git_repo(origin)
    _git(origin, "commit", "-q", "--allow-empty", "-m", "first")
    before = _git_rev(origin)
    write(origin, "new.md", "## New\n" + "content " * 5)
    _git(origin, "add", "-A")
    _git(origin, "commit", "-q", "-m", "second")

    with docimport.checkout(f"file://{origin}", shallow=True) as root, \
            pytest.raises(RuntimeError):
        docimport.changed_markdown_files(root, before)


def test_checkout_full_clone_can_diff_against_an_earlier_ref(tmp_path):
    origin = tmp_path / "origin"
    origin.mkdir()
    _init_git_repo(origin)
    _git(origin, "commit", "-q", "--allow-empty", "-m", "first")
    before = _git_rev(origin)
    write(origin, "new.md", "## New\n" + "content " * 5)
    _git(origin, "add", "-A")
    _git(origin, "commit", "-q", "-m", "second")

    with docimport.checkout(f"file://{origin}", shallow=False) as root:
        changed = docimport.changed_markdown_files(root, before)
        assert [p.name for p in changed] == ["new.md"]


def test_checkout_raises_a_clear_error_on_a_bad_remote(tmp_path):
    with pytest.raises(RuntimeError), \
            docimport.checkout(f"file://{tmp_path / 'does-not-exist'}"):
        pass


def test_cli_import_docs_works_against_a_remote_url(tmp_path, env):
    origin = tmp_path / "origin"
    origin.mkdir()
    _init_git_repo(origin)
    write(origin, "guide.md", "## Guide\n" + "operational content " * 5)
    _git(origin, "add", "-A")
    _git(origin, "commit", "-q", "-m", "baseline")

    cli.main(["import-docs", f"file://{origin}", "--apply"])

    assert len(env.nodes) == 1
    (node,) = env.nodes.values()
    assert node["links"] == ["guide.md"]


def test_cli_import_docs_since_works_against_a_remote_url(tmp_path, env):
    origin = tmp_path / "origin"
    origin.mkdir()
    _init_git_repo(origin)
    write(origin, "unchanged.md", "## Old\n" + "never touched again " * 5)
    _git(origin, "add", "-A")
    _git(origin, "commit", "-q", "-m", "baseline")
    before = _git_rev(origin)

    write(origin, "new.md", "## New\n" + "brand new content " * 5)
    _git(origin, "add", "-A")
    _git(origin, "commit", "-q", "-m", "add new.md")

    cli.main(["import-docs", f"file://{origin}", "--since", before, "--apply"])

    assert len(env.nodes) == 1
    (node,) = env.nodes.values()
    assert node["links"] == ["new.md"]


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


def test_exclude_keeps_a_scoped_apply_from_touching_the_excluded_files(tmp_path, env):
    write(tmp_path, "AGENTS.md", "## Standards\n" + "real standard " * 5)
    write(tmp_path, "docs/blog/launch.md", "## Launch\n" + "marketing copy " * 5)

    cli.main(["import-docs", str(tmp_path), "--exclude", "docs/blog/*", "--apply"])

    assert len(env.nodes) == 1
    (node,) = env.nodes.values()
    assert node["links"] == ["AGENTS.md"]


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
