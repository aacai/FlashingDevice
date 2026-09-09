import pytest

usb = pytest.importorskip("usb.core")

from flash_device.utils.usb import probe_9008


def test_probe_no_device(monkeypatch):
    monkeypatch.setattr(usb, "find", lambda *a, **k: None)
    assert probe_9008() == (False, "no-device")


def test_probe_result_shape():
    # 真机在场与否不确定，只断言返回结构合法（CI 无设备走 no-device，本地按实测走）。
    ok, detail = probe_9008(sniff_ms=200, nop_ms=500)
    assert isinstance(ok, bool) and isinstance(detail, str) and detail
    assert detail.split(":")[0] in (
        "no-device",
        "sahara",
        "sahara-unrestored",
        "firehose",
        "silent",
        "busy",
        "no-bulk-endpoints",
        "alive-0x",
        "alive-nop",
        "error",
    ) or detail.startswith(("alive-", "error"))
