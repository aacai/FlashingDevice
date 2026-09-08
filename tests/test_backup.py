from flash_device.safety.backup import write_manifest


def test_manifest(tmp_path):
    d = tmp_path / "b"
    d.mkdir()
    (d / "gpt_main0.bin").write_bytes(b"hello")
    mp = write_manifest(str(d), [{"op": "gpt"}])
    assert "MANIFEST.json" in mp
    assert (d / "sha256sums.txt").exists()
