from flash_device.devices import loaders


def test_scan_and_library(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    # Hermetic: CI checks out the full Loaders submodule (481 files),
    # so pin the repo dir away instead of assuming it's absent.
    monkeypatch.setattr(loaders, "repo_loaders_dir", lambda: "")
    assert loaders.scan([]) == []
    # drop a fake loader somewhere,入库, rescan finds it
    srcdir = tmp_path / "fw"
    srcdir.mkdir()
    elf = srcdir / "prog_firehose_test.elf"
    elf.write_bytes(b"\x7fELF" + b"\0" * 100)
    dst = loaders.copy_into_library(str(elf))
    assert dst.startswith(loaders.library_dir())
    found = loaders.scan([])
    assert len(found) == 1 and found[0].name == "prog_firehose_test.elf"
    assert len(found[0].sha256) == 64
    # dedupe: same content elsewhere collapses
    elf2 = srcdir / "copy.elf"
    elf2.write_bytes(b"\x7fELF" + b"\0" * 100)
    assert len(loaders.scan([str(srcdir)])) == 1
