import os

from flash_device.utils import envcheck, logging_setup


def test_envcheck_never_crashes():
    checks = envcheck.run_all_checks()
    names = [c.name for c in checks]
    assert "Python >= 3.10" in names
    assert "EDL 子模块" in names
    text = envcheck.format_checks(checks)
    assert "✅" in text or "❌" in text


def test_logging_creates_file(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    path = logging_setup.setup_logging("DEBUG")
    assert os.path.isfile(path)
    assert logging_setup.get_log_path() == path
