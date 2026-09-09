import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from flash_device import state as shared
from flash_device.server import Handler


def test_state_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert shared.load()["loader"] == ""
    st = shared.save({"loader": "/x/y.elf", "fwdir": "/fw", "memory": "emmc"}, by="t")
    assert st["loader"] == "/x/y.elf" and st["updated_by"] == "t"
    assert shared.load()["memory"] == "emmc"
    # 空值不覆盖
    shared.save({"loader": ""}, by="t")
    assert shared.load()["loader"] == "/x/y.elf"
    p = tmp_path / ".flash-device" / "state.json"
    assert json.loads(p.read_text())["fwdir"] == "/fw"


def _post(url, obj):
    req = urllib.request.Request(
        url,
        data=json.dumps(obj).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def _get(url):
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def test_server_state_api(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{port}"
        code, body = _get(base + "/api/state")
        assert code == 200 and body["memory"] == "ufs"
        code, body = _post(base + "/api/state", {"loader": "/a.elf", "junk": 1})
        assert code == 200 and body["loader"] == "/a.elf"
        code, _ = _get(base + "/api/status")
        assert code == 200
        code, body = _post(base + "/api/state", {})
        assert code == 400
        code, body = _get(base + "/nope")
        assert code == 404
    finally:
        srv.shutdown()
