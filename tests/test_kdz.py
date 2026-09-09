import json
import os

from flash_device.devices import kdz


def _fake_kdz(tmp_path, model="V500N30c", carrier="LGU"):
    p = tmp_path / "fake.kdz"
    blob = (
        b'\x28\x05\x00\x00$8"%'
        + f"{model}_0_user-signed-ARB0_{carrier}_KR_OP_1021.dz".encode()
        + b"\x00" * 100
    )
    p.write_bytes(blob + b"\x00" * 70000)
    return str(p)


def test_parse_kdz_header_ok(tmp_path):
    info = kdz.parse_kdz_header(_fake_kdz(tmp_path))
    assert info["ok"] and info["model"] == "V500N30c" and info["carrier"] == "LGU"


def test_parse_kdz_header_rejects(tmp_path):
    bad = tmp_path / "x.kdz"
    bad.write_bytes(b"not a kdz" * 10)
    assert not kdz.parse_kdz_header(str(bad))["ok"]
    assert not kdz.parse_kdz_header(str(tmp_path / "nope.kdz"))["ok"]


def test_gen_rawprograms_from_metadata(tmp_path):
    d = tmp_path / "fw"
    d.mkdir()
    meta = {
        "dz": {
            "parts": {
                "0": {
                    "system_a": [{"start_sector": 100, "sector_count": 8}],
                    "PrimaryGPT": [{"start_sector": 0, "sector_count": 6}],
                },
                "4": {
                    "boot_a": [
                        {"start_sector": 50, "sector_count": 4},
                        {"start_sector": 60, "sector_count": 4},
                    ]
                },
            }
        }
    }
    (d / "metadata.json").write_text(json.dumps(meta))
    made = kdz.gen_rawprograms(str(d))
    assert len(made) == 2
    rp0 = (d / "rawprogram0.xml").read_text()
    assert 'filename="0.system_a.img"' in rp0 and 'start_sector="100"' in rp0
    assert 'partofsingleimage="true"' in rp0  # GPT entry
    rp4 = (d / "rawprogram4.xml").read_text()
    assert 'num_partition_sectors="14"' in rp4  # 50..68 span across gap


def test_gen_missing_metadata(tmp_path):
    import pytest

    with pytest.raises(FileNotFoundError):
        kdz.gen_rawprograms(str(tmp_path))


def test_find_extractor_never_crashes(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("KDZ_TOOL", raising=False)
    path, hint = kdz.find_extractor()
    assert isinstance(path, str) and isinstance(hint, str)


def test_real_lgu_header():
    p = "/Users/zhiqiu/AndroidStudioProjects/shuaji/[up_addROM.com]_V500N30c_00_LGU_KR_OP_1021.kdz"
    if not os.path.exists(p):
        import pytest

        pytest.skip("needs local KDZ")
    info = kdz.parse_kdz_header(p)
    assert info["ok"] and info["carrier"] == "LGU" and info["model"] == "V500N30c"
