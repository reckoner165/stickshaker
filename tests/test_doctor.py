"""doctor treats each agent as optional, but needs at least one set up."""

from stickshaker import doctor, install


def run(tmp_path, capsys, claude: bool, codex: bool):
    claude_path, codex_path = tmp_path / "settings.json", tmp_path / "hooks.json"
    for enabled, target, path in ((claude, install.CLAUDE, claude_path), (codex, install.CODEX, codex_path)):
        if enabled:
            s: dict = {}
            install.add_hooks(s, debug=False, target=target)
            install.save(path, s)
    code = doctor.run(tmp_path / "no.sock", claude_path, codex_path, tmp_path / "memories")
    return code, capsys.readouterr().out


def errors(out):
    return [line for line in out.splitlines() if line.startswith("ERR") and "socket" not in line]


def test_claude_only_passes_hook_checks(tmp_path, capsys):
    _, out = run(tmp_path, capsys, claude=True, codex=False)
    assert errors(out) == []


def test_codex_only_passes_hook_checks(tmp_path, capsys):
    _, out = run(tmp_path, capsys, claude=False, codex=True)
    assert errors(out) == []


def test_no_agent_is_an_error(tmp_path, capsys):
    _, out = run(tmp_path, capsys, claude=False, codex=False)
    assert any("no agent hooks installed" in line for line in errors(out))


def test_partial_install_is_an_error(tmp_path, capsys):
    path = tmp_path / "settings.json"
    s: dict = {}
    install.add_hooks(s, debug=False, target=install.CLAUDE)
    del s["hooks"]["PostCompact"]
    install.save(path, s)
    doctor.run(tmp_path / "no.sock", path, tmp_path / "hooks.json", tmp_path / "memories")
    assert any("PostCompact hook missing" in line for line in errors(capsys.readouterr().out))
