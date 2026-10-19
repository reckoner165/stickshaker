import json

from stickshaker import install

OTHER_HOOK = {"type": "command", "command": "echo other"}


def test_install_into_empty_settings():
    s: dict = {}
    install.add_hooks(s, debug=False)
    assert set(s["hooks"]) == {"PreCompact", "SessionStart", "PostToolUse"}
    assert s["hooks"]["SessionStart"][0]["matcher"] == "compact"
    assert "matcher" not in s["hooks"]["PreCompact"][0]
    hook = s["hooks"]["PostToolUse"][0]["hooks"][0]
    assert hook["command"].endswith("|| true") and hook["timeout"] == install.HOOK_TIMEOUT


def test_install_keeps_other_settings_and_hooks():
    s = {"model": "opus", "hooks": {"PostToolUse": [{"matcher": "Bash", "hooks": [OTHER_HOOK]}]}}
    install.add_hooks(s, debug=False)
    assert s["model"] == "opus"
    assert s["hooks"]["PostToolUse"][0] == {"matcher": "Bash", "hooks": [OTHER_HOOK]}
    assert len(s["hooks"]["PostToolUse"]) == 2


def test_install_twice_is_idempotent():
    s: dict = {}
    install.add_hooks(s, debug=False)
    once = json.dumps(s, sort_keys=True)
    install.add_hooks(s, debug=False)
    assert json.dumps(s, sort_keys=True) == once


def test_switch_to_debug_replaces_in_place():
    s: dict = {}
    install.add_hooks(s, debug=False)
    install.add_hooks(s, debug=True)
    cmds = install.installed_hooks(s)
    assert len(cmds) == 3 and all(not c.endswith("|| true") for c in cmds.values())
    assert sum(len(g["hooks"]) for gs in s["hooks"].values() for g in gs) == 3


def test_uninstall_removes_only_ours_and_prunes():
    s = {"model": "opus", "hooks": {"PostToolUse": [{"matcher": "Bash", "hooks": [OTHER_HOOK]}]}}
    install.add_hooks(s, debug=False)
    assert install.remove_hooks(s) == 3
    assert s == {"model": "opus", "hooks": {"PostToolUse": [{"matcher": "Bash", "hooks": [OTHER_HOOK]}]}}


def test_uninstall_from_only_ours_drops_hooks_key():
    s = {"model": "opus"}
    install.add_hooks(s, debug=False)
    install.remove_hooks(s)
    assert s == {"model": "opus"}


def test_save_backs_up_and_writes(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text('{"model": "opus"}')
    s = install.load(p)
    install.add_hooks(s, debug=False)
    backup = install.save(p, s)
    assert backup is not None and json.loads(backup.read_text()) == {"model": "opus"}
    assert install.installed_hooks(install.load(p)).keys() == {"PreCompact", "SessionStart", "PostToolUse"}
    assert not (tmp_path / "settings.json.stickshaker-tmp").exists()


def test_load_missing_file_is_empty(tmp_path):
    assert install.load(tmp_path / "nope.json") == {}
