"""The manifests in plugin/ must be exactly what `agenthive install` writes.

If these drift, somebody reading the repo on GitHub and somebody running
the installer end up with different hooks, and only one of them works.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

from agenthive_mcp import manifests

REPO = os.path.join(os.path.dirname(__file__), "..")


@pytest.mark.parametrize("harness", sorted(manifests.MANIFESTS))
def test_repo_copy_matches_generated(harness):
    with open(os.path.join(REPO, manifests.REPO_PATHS[harness])) as fh:
        assert json.load(fh) == manifests.MANIFESTS[harness]


@pytest.mark.parametrize("harness", sorted(manifests.MANIFESTS))
def test_every_manifest_runs_the_hook(harness):
    assert manifests.HOOK_COMMAND in json.dumps(manifests.MANIFESTS[harness])


def test_destinations_are_absolute(tmp_path):
    for harness in manifests.MANIFESTS:
        assert manifests.destination(harness, str(tmp_path)).is_absolute()


def test_unknown_harness_is_an_error():
    with pytest.raises(ValueError):
        manifests.destination("emacs")


def test_merge_into_empty_config_installs_everything():
    merged, changed = manifests.merge(None, "claude")
    assert merged == manifests.MANIFESTS["claude"]
    assert sorted(changed) == ["PostToolUse", "SessionEnd", "SessionStart"]


def test_merge_keeps_other_peoples_settings_and_hooks():
    existing = {
        "theme": "dark",
        "hooks": {
            "SessionStart": [{"hooks": [{"type": "command", "command": "their-tool"}]}],
            "Notification": [{"hooks": [{"type": "command", "command": "beep"}]}],
        },
    }
    merged, changed = manifests.merge(existing, "claude")

    assert merged["theme"] == "dark"
    assert merged["hooks"]["Notification"] == existing["hooks"]["Notification"]
    # Their SessionStart hook survives, ours is appended alongside it.
    start = json.dumps(merged["hooks"]["SessionStart"])
    assert "their-tool" in start
    assert manifests.HOOK_COMMAND in start
    assert "SessionStart" in changed


def test_merge_is_idempotent():
    once, _ = manifests.merge(None, "cursor")
    twice, changed = manifests.merge(once, "cursor")
    assert twice == once
    assert changed == []


def test_merge_will_not_clobber_a_shape_it_cannot_parse():
    existing = {"hooks": {"SessionStart": "surprise"}}
    merged, changed = manifests.merge(existing, "claude")
    assert merged["hooks"]["SessionStart"] == "surprise"
    assert "SessionStart" not in changed


def test_uninstall_removes_only_our_entries():
    existing = {
        "theme": "dark",
        "hooks": {
            "SessionStart": [{"hooks": [{"type": "command", "command": "their-tool"}]}],
        },
    }
    installed, _ = manifests.merge(existing, "claude")
    removed, changed = manifests.uninstall(installed, "claude")

    assert manifests.HOOK_COMMAND not in json.dumps(removed)
    assert "their-tool" in json.dumps(removed["hooks"]["SessionStart"])
    assert removed["theme"] == "dark"
    assert "SessionStart" in changed


def test_uninstall_on_a_clean_config_changes_nothing():
    existing = {"hooks": {"SessionStart": [{"hooks": [{"command": "theirs"}]}]}}
    removed, changed = manifests.uninstall(existing, "claude")
    assert removed == existing
    assert changed == []


def test_render_is_newline_terminated_json():
    text = manifests.render("vscode")
    assert text.endswith("\n")
    assert json.loads(text) == manifests.MANIFESTS["vscode"]
