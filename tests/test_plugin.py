"""
The cross-harness plugin: event normalisation, facts, tool pruning, the
loop guard.

The property every one of these defends is the same: a hook must never
break the session. A memory system that can take somebody's agent down
is worse than no memory system, and the failure gets blamed on the
agent rather than on us.
"""
import io
import json
import os
import sys

import pytest

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC)

from agenthive_mcp import cli, facts, hooks, loopguard, plugin, toolprune


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTHIVE_HOOK_STATE_DIR", str(tmp_path / "state"))
    for key in ("AGENTHIVE_URL", "AGENTHIVE_TOKEN", "AGENTHIVE_TEAM_ID",
                "AGENTHIVE_HOST"):
        monkeypatch.delenv(key, raising=False)


# --- normalisation: one shape, three harnesses --------------------------

def test_claude_and_vscode_share_a_format():
    """VS Code uses the same hook format as Claude Code, so one handler
    serves both and nothing is duplicated per IDE."""
    for raw in ("SessionStart", "sessionstart"):
        e = hooks.normalise({"hook_event_name": raw, "cwd": "/tmp"})
        assert e["event"] == hooks.SESSION_START


def test_cursor_events_are_translated_not_duplicated():
    os.environ["AGENTHIVE_HOST"] = "cursor"
    try:
        assert hooks.normalise({"hook_event_name": "afterFileEdit"})["event"] == hooks.TOOL_RESULT
        assert hooks.normalise({"hook_event_name": "stop"})["event"] == hooks.SESSION_END
    finally:
        del os.environ["AGENTHIVE_HOST"]


def test_the_host_can_be_forced_because_stop_is_ambiguous():
    """`stop` exists in both families and carries no marker. A wrong
    guess turns session-end into a silent no-op, so the env var wins."""
    os.environ["AGENTHIVE_HOST"] = "cursor"
    try:
        assert hooks.detect_host({"hook_event_name": "stop"}) == "cursor"
    finally:
        del os.environ["AGENTHIVE_HOST"]


def test_session_id_is_found_whatever_the_harness_calls_it():
    for key in ("session_id", "conversationId", "threadId"):
        assert hooks.normalise({key: "abc"})["session_id"] == "abc"


def test_garbage_on_stdin_is_an_empty_event_not_a_crash():
    e = hooks.read_event(io.StringIO("this is not json{{{"))
    assert e["event"] and e["payload"] == {}


# --- facts: the part that actually saves tokens -------------------------

def test_facts_render_as_a_context_block():
    block = facts.render({"facts": [
        {"body": "tests: python -m pytest -q"},
        {"body": "fixtures live in conftest.py"},
    ]})
    assert "python -m pytest -q" in block
    assert block.startswith("What this team already knows")


def test_the_fact_block_is_capped():
    """Injected into every session unconditionally, so a block that
    grew without limit would reintroduce the cost this removes."""
    many = {"facts": [{"body": "x" * 200} for _ in range(50)]}
    block = facts.render(many, budget=100)
    assert len(block) // 4 <= 140


def test_facts_are_dropped_from_the_least_confirmed_end():
    """They arrive ordered by corroboration, so truncating the tail
    drops the weakest first."""
    payload = {"facts": [{"body": "FIRST fact"}, {"body": "y" * 2000}]}
    block = facts.render(payload, budget=20)
    assert "FIRST fact" in block


def test_no_facts_means_no_block_at_all():
    assert facts.render({"facts": []}) == ""


def test_an_unreachable_server_yields_no_facts_not_an_error():
    """This runs at session start. A session that cannot reach
    AgentHive should begin normally, not fail to begin."""
    os.environ.update(AGENTHIVE_URL="http://127.0.0.1:9",
                      AGENTHIVE_TOKEN="t", AGENTHIVE_TEAM_ID="team")
    try:
        assert facts.fetch("/tmp") == {"facts": [], "approx_tokens": 0}
    finally:
        for k in ("AGENTHIVE_URL", "AGENTHIVE_TOKEN", "AGENTHIVE_TEAM_ID"):
            del os.environ[k]


def test_a_repeated_failure_then_a_success_becomes_a_proposal(tmp_path):
    """One failure is a typo. The same one twice, with a different
    command then working, is knowledge."""
    cwd = str(tmp_path)
    for _ in range(2):
        facts.observe_failure(cwd, "pytest -q", "bash: pytest: command not found")
    proposal = facts.observe_success(cwd, "python -m pytest -q")
    assert proposal is not None
    assert "env-fact" in proposal["tags"]
    assert "python -m pytest" in proposal["body"]


def test_a_single_failure_proposes_nothing(tmp_path):
    cwd = str(tmp_path)
    facts.observe_failure(cwd, "pytest -q", "command not found")
    assert facts.observe_success(cwd, "python -m pytest -q") is None


def test_uninteresting_commands_are_ignored(tmp_path):
    cwd = str(tmp_path)
    for _ in range(5):
        facts.observe_failure(cwd, "ls /nope", "No such file or directory")
    assert facts.observe_success(cwd, "ls /tmp") is None


# --- tool pruning -------------------------------------------------------

def test_pruning_writes_only_into_the_harness_settings(tmp_path):
    ok, why = toolprune.apply("claude", str(tmp_path), ["Browser", "Notebook"])
    assert ok, why
    data = json.loads((tmp_path / ".claude/settings.local.json").read_text())
    assert set(data["permissions"]["deny"]) == {"Browser", "Notebook"}
    assert set(data["agenthive"]["pruned"]) == {"Browser", "Notebook"}


def test_restore_takes_back_only_what_the_plugin_wrote(tmp_path):
    """Losing somebody's own permission rule to a cost optimisation
    would be unforgivable."""
    path = tmp_path / ".claude/settings.local.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"permissions": {"deny": ["MyOwnRule"]}}))

    toolprune.apply("claude", str(tmp_path), ["Browser"])
    ok, _ = toolprune.restore("claude", str(tmp_path))
    assert ok
    assert json.loads(path.read_text())["permissions"]["deny"] == ["MyOwnRule"]


def test_an_unparseable_settings_file_is_left_alone(tmp_path):
    """Somebody's work in progress is never worth a deny list."""
    path = tmp_path / ".claude/settings.local.json"
    path.parent.mkdir(parents=True)
    path.write_text("{ this is not json")
    ok, why = toolprune.apply("claude", str(tmp_path), ["Browser"])
    assert not ok and "not valid JSON" in why
    assert path.read_text() == "{ this is not json"


def test_an_unknown_harness_refuses_rather_than_guessing(tmp_path):
    ok, why = toolprune.apply("emacs", str(tmp_path), ["Browser"])
    assert not ok and "no settings target" in why


# --- loop guard ---------------------------------------------------------

def test_the_same_failure_repeating_earns_one_redirect(tmp_path):
    out = "Traceback: AssertionError at line 42"
    assert loopguard.observe("s", str(tmp_path), out) is None
    assert loopguard.observe("s", str(tmp_path), out) is None
    third = loopguard.observe("s", str(tmp_path), out)
    assert third and "not going to start working" in third["context"]


def test_a_second_nudge_is_never_sent(tmp_path):
    """A nudge after the first was ignored is noise, and noise trains
    people to ignore the first."""
    out = "ERROR: the same thing again"
    nudges = [loopguard.observe("s", str(tmp_path), out) for _ in range(6)]
    with_context = [n for n in nudges if n and n.get("context")]
    assert len(with_context) == 1


def test_a_stuck_turn_is_reported_to_the_human_not_stopped(tmp_path):
    out = "FAILED again"
    for _ in range(8):
        last = loopguard.observe("s", str(tmp_path), out)
    assert last and last.get("tell_user") and "Esc to stop it" in last["tell_user"]
    assert not last.get("context")


def test_noise_that_is_not_a_failure_is_ignored(tmp_path):
    for _ in range(10):
        assert loopguard.observe("s", str(tmp_path), "all tests passed") is None


def test_the_same_error_at_a_different_line_still_counts_as_a_repeat(tmp_path):
    """A literal comparison would miss the most common shape of a real
    loop."""
    for n in (10, 20, 30):
        last = loopguard.observe("s", str(tmp_path),
                                 f"AssertionError at line {n} in /tmp/x{n}.py")
    assert last is not None


# --- the entry point never breaks a session -----------------------------

def _run_hook(monkeypatch, payload):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    return plugin.main()


def test_every_event_exits_zero(monkeypatch):
    for event in ("SessionStart", "PostToolUse", "SessionEnd", "Stop", "nonsense"):
        assert _run_hook(monkeypatch, {"hook_event_name": event, "cwd": "/tmp"}) == 0


def test_a_broken_handler_cannot_break_the_session(monkeypatch):
    monkeypatch.setattr(plugin, "on_session_start",
                        lambda e: (_ for _ in ()).throw(RuntimeError("boom")))
    assert _run_hook(monkeypatch, {"hook_event_name": "SessionStart"}) == 0


def test_mechanisms_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("AGENTHIVE_FACTS", "off")
    assert plugin.on_session_start({"cwd": "/tmp", "payload": {}}) == ""


def test_emit_writes_both_dialects(capsys):
    """A key the other harness does not recognise is ignored; a hook
    that stays silent when it had something to say has done nothing."""
    hooks.emit(context="hello")
    out = json.loads(capsys.readouterr().out)
    assert out["context"] == "hello"
    assert out["hookSpecificOutput"]["additionalContext"] == "hello"


# --- the CLI ------------------------------------------------------------

def test_the_cli_says_what_is_wrong_when_unconfigured(capsys):
    assert cli.main(["doctor"]) == 1
    assert "does NOT inherit" in capsys.readouterr().out


def test_an_unknown_command_lists_the_real_ones(capsys):
    assert cli.main(["nonsense"]) == 2
    assert "status" in capsys.readouterr().out
