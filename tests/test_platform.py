import sys

from flash_device.utils import envcheck
from flash_device.utils import platform as pf


def test_resolve_edl_dev_uses_repo_submodule():
    assert not pf.is_frozen()
    # In this checkout the submodule exists (CI uses submodules: recursive).
    assert pf.resolve_edl_bin().endswith("third_party/edl/edl.py")


def test_resolve_edl_frozen_uses_bundle(tmp_path, monkeypatch):
    engine = tmp_path / "third_party" / "edl" / "edl.py"
    engine.parent.mkdir(parents=True)
    engine.write_text("# fake engine")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert pf.is_frozen()
    assert pf.resolve_edl_bin() == str(engine)
    assert "python3" in pf.python_for_edl()


def test_envcheck_has_edl_python_gate():
    checks = envcheck.run_all_checks()
    assert any(c.name == "EDL 解释器" for c in checks)
