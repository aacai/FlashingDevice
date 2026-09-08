from flash_device.backend.qfil import PLACEHOLDER_PATCH, build_qfil_argv


def _fw(tmp_path, n_raw=2, with_patch=False):
    d = tmp_path / "KT"
    d.mkdir()
    for i in range(n_raw):
        (d / f"rawprogram{i}.xml").write_text(
            '<?xml version="1.0" ?><data>'
            f'<program filename="0.sys{i}.img" label="sys{i}" '
            'num_partition_sectors="8" start_sector="0" SECTOR_SIZE_IN_BYTES="4096"/>'
            "</data>"
        )
        (d / f"0.sys{i}.img").write_bytes(b"\0" * 64)
    if with_patch:
        (d / "patch0.xml").write_text('<?xml version="1.0" ?><data></data>')
    loader = tmp_path / "prog_firehose_test.elf"
    loader.write_bytes(b"\x7fELF")
    return str(d), str(loader)


def test_qfil_multi_lun_joined(tmp_path):
    fw, loader = _fw(tmp_path)
    args, total, err = build_qfil_argv(fw, loader, "ufs")
    assert err == ""
    assert total == 2
    qi = args.index("qfil")
    raws = args[qi + 1].split(",")
    assert len(raws) == 2 and all(r.endswith(".xml") for r in raws)
    assert args[qi + 2] == PLACEHOLDER_PATCH  # LG dumps ship no patch files
    assert args[qi + 3] == fw
    assert args[-1] == "--memory=ufs"


def test_qfil_uses_real_patch_when_present(tmp_path):
    fw, loader = _fw(tmp_path, with_patch=True)
    args, _total, err = build_qfil_argv(fw, loader, "ufs")
    assert err == ""
    assert args[args.index("qfil") + 2].endswith("patch0.xml")


def test_qfil_rejects_bad_loader(tmp_path):
    fw, _ = _fw(tmp_path)
    _, _, err = build_qfil_argv(fw, "/nonexistent/x.elf", "ufs")
    assert err
