from flash_device.safety import guards


def test_avb_gate_set():
    assert guards.needs_avb_warning("boot_a")
    assert guards.needs_avb_warning("BOOT_B")
    assert guards.needs_avb_warning("vbmeta_system_a")
    assert guards.needs_avb_warning("init_boot")
    assert guards.needs_avb_warning("dtbo_b")
    assert not guards.needs_avb_warning("system_a")
    assert not guards.needs_avb_warning("persist")
    assert not guards.needs_avb_warning("")


def test_avb_text_orange_is_reassuring():
    t = guards.avb_warning_text("boot_a", {"verifiedbootstate": "orange", "device_state": "unlocked"})
    assert "黄字" in t and "boot_a" in t


def test_avb_text_green_is_strict():
    t = guards.avb_warning_text("vbmeta_a", {"verifiedbootstate": "green", "device_state": "locked"})
    assert "拒绝开机" in t or "红字" in t


def test_avb_text_unknown_is_strictest():
    t = guards.avb_warning_text("boot_a", {})
    assert "查不到" in t and "9008" in t


def test_read_avb_state_no_adb(monkeypatch):
    import shutil

    from flash_device.utils import adb as adbmod

    monkeypatch.setattr(shutil, "which", lambda *_a, **_k: None)
    assert adbmod.read_avb_state() == {}
    assert adbmod.first_device() == ""


def test_read_avb_state_no_device(monkeypatch):
    from flash_device.utils import adb as adbmod

    monkeypatch.setattr(adbmod.shutil, "which", lambda *_a, **_k: "/fake/adb")

    def fake_run(*_a, **_k):
        class R:
            returncode = 0
            stdout = "List of devices attached\n\n"

        return R()

    monkeypatch.setattr(adbmod.subprocess, "run", fake_run)
    assert adbmod.read_avb_state() == {}
