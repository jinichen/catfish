from catfish_tool_bridge import email_runtime


def test_published_generation_and_invalid_pointer(tmp_path):
    root = tmp_path / 'email-runtime'
    name = 'env-' + 'a' * 32
    scripts = root / name / 'Scripts'
    scripts.mkdir(parents=True)
    pointer = root / 'current.txt'
    pointer.write_text(name)
    assert email_runtime.isolated_scripts(tmp_path) is None
    for exe in ('python.exe', 'catfish-email.exe'):
        (scripts / exe).touch()
    assert email_runtime.isolated_scripts(tmp_path) == scripts
    pointer.write_text('../hermes-agent/venv')
    assert email_runtime.isolated_scripts(tmp_path) is None


def test_windows_isolated_cli_precedes_legacy_path(monkeypatch, tmp_path):
    name = 'env-' + 'f' * 32
    scripts = tmp_path / 'email-runtime' / name / 'Scripts'
    scripts.mkdir(parents=True)
    (scripts.parent.parent / 'current.txt').write_text(name)
    for exe in ('python.exe', 'catfish-email.exe'):
        (scripts / exe).touch()
    monkeypatch.delenv('CATFISH_EMAIL_BIN', raising=False)
    monkeypatch.setattr(email_runtime.sys, 'platform', 'win32')
    monkeypatch.setattr(email_runtime, 'hermes_home', lambda: tmp_path)
    monkeypatch.setattr(email_runtime.shutil, 'which', lambda _: 'old.exe')
    assert email_runtime.find_email_cli() == str(scripts / 'catfish-email.exe')
