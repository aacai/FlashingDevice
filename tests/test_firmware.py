import os

from flash_device.devices.firmware import validate_fwdir

KT = "/Users/zhiqiu/AndroidStudioProjects/shuaji/lg_v50/extract/KT"
LGU = "/Users/zhiqiu/AndroidStudioProjects/shuaji/lg_v50/extract/LGU"
SKT = "/Users/zhiqiu/AndroidStudioProjects/shuaji/lg_v50/extract/SKT"
PIPA = (
    "/Users/zhiqiu/AndroidStudioProjects/shuaji/firmware/pipa_images_OS2.0.20.0.UMZCNXM_14.0/images"
)
LG_LOADER = (
    "/Users/zhiqiu/AndroidStudioProjects/shuaji/edl-tools/edl/Loaders/LG/"
    "000a50e100310000_e746e34f737403f4_fhprg_lg_g8x.bin"
)
PIPA_LOADER = os.path.join(PIPA, "prog_ufs_firehose_sm8250_ddr_5.elf")


def show(label, d, loader):
    rep = validate_fwdir(d, loader)
    print(f"== {label} [{rep.level}] {rep.summary()}")
    for e in rep.errors:
        print(f"   ⛔ {e}")
    for w in rep.warnings:
        print(f"   ⚠ {w}")


def test_lg_dumps_warn_no_patch_but_ok():
    # LG KDZ 解包：无 patch、无包内 loader → warn，但文件齐全可刷
    rep = validate_fwdir(KT, LG_LOADER)
    assert rep.kind == "qfil" and rep.level == "warn", rep
    assert rep.file_count == 74
    assert any("patch" in w for w in rep.warnings)


def test_lgu_and_skt_same_shape():
    for d in (LGU, SKT):
        rep = validate_fwdir(d, LG_LOADER)
        assert rep.kind == "qfil" and rep.ok, (d, rep.errors)
        assert rep.file_count == 74


def test_pipa_in_package_loader_ok():
    # 小米包：自带 loader+patch → 全绿（loader 传空，靠包内自带）
    rep = validate_fwdir(PIPA, "")
    assert rep.kind == "qfil" and rep.level == "ok", (rep.errors, rep.warnings)
    assert rep.file_count == 47
    assert "自带" in rep.loader_hint


def test_broken_dir_blocked(tmp_path):
    d = tmp_path / "broken"
    d.mkdir()
    (d / "rawprogram0.xml").write_text(
        '<?xml version="1.0" ?><data><program filename="nope.img" '
        'num_partition_sectors="8" start_sector="0"/></data>'
    )
    rep = validate_fwdir(str(d), "")
    assert rep.level == "error"
    assert any("nope.img" in e for e in rep.errors)


def test_empty_and_fastboot_dirs_blocked(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert validate_fwdir(str(empty)).level == "error"
    fb = tmp_path / "fb"
    (fb / "images").mkdir(parents=True)
    (fb / "flash_all.sh").write_text("#!/bin/sh\n")
    (fb / "images" / "super.img").write_bytes(b"\0")
    rep = validate_fwdir(str(fb))
    assert rep.kind == "fastboot" and rep.level == "error"
