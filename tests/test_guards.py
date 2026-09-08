from flash_device.safety import guards


def test_validate_loader_missing():
    assert not guards.validate_loader("").ok
    assert not guards.validate_loader("/nonexistent/x.elf").ok


def test_validate_loader_ok(tmp_path):
    p = tmp_path / "prog_firehose_ddr.elf"
    p.write_bytes(b"\x7fELF fake")
    assert guards.validate_loader(str(p)).ok


def test_critical_partition_blocked(tmp_path):
    img = tmp_path / "x.img"
    img.write_bytes(b"123")
    r = guards.validate_single_write("modemst1", str(img))
    assert not r.ok


def test_single_write_bad_name(tmp_path):
    img = tmp_path / "x.img"
    img.write_bytes(b"123")
    assert not guards.validate_single_write("boot;a", str(img)).ok


def test_classify():
    assert guards.classify_pid(0x9008) == "edl"
    assert guards.classify_pid(0x4EE0) == "fastboot"
