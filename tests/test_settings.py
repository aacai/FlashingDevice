from flash_device import settings as S


def test_defaults():
    d = S.load({})
    assert d == dict(S.DEFAULTS)


def test_coercion_from_ini_strings():
    d = S.load(
        {
            "server_autostart": "false",
            "server_port": "9999",
            "expected_serial": " ab12 ",
            "log_level": "debug",
            "edl_bin": " /x ",
            "kdz_tool": "",
            "unknown": 1,
        }
    )
    assert d["server_autostart"] is False
    assert d["server_port"] == 9999
    assert d["expected_serial"] == "AB12"
    assert d["log_level"] == "DEBUG"
    assert d["edl_bin"] == "/x"
    assert "unknown" not in d


def test_port_clamped_and_level_fallback():
    assert S.load({"server_port": "1"})["server_port"] == S.MIN_PORT
    assert S.load({"server_port": "99999"})["server_port"] == S.MAX_PORT
    assert S.load({"server_port": "abc"})["server_port"] == 8899
    assert S.load({"log_level": "VERBOSE"})["log_level"] == "INFO"
    assert S.load({"server_autostart": "yes"})["server_autostart"] is True


def test_dump_roundtrip():
    vals = {
        "server_autostart": False,
        "server_port": 9999,
        "edl_bin": "",
        "kdz_tool": "",
        "expected_serial": "6a738fee",
        "log_level": "WARNING",
    }
    assert S.dump(vals) == {**vals, "expected_serial": "6A738FEE"}
    assert S.load(S.dump(vals)) == S.dump(vals)
