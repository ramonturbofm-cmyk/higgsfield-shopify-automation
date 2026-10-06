"""Desktop launcher + HTML report (the part packaged as EnergyManager.exe)."""

import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import date

import pytest

from ems.desktop.app import DesktopApp, ensure_user_config, main, make_server
from ems.report import _nice_ticks


def test_nice_ticks_cover_range():
    for lo, hi in [(20.7, 22.5), (-4.2, 11.3), (0, 100), (0.02, 0.157), (5, 5)]:
        ticks = _nice_ticks(lo, hi)
        assert ticks[0] <= lo and ticks[-1] >= hi


@pytest.fixture
def server(tmp_path):
    app = DesktopApp(ensure_user_config(tmp_path), tmp_path)
    srv = make_server(app, 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield app
    srv.shutdown()
    srv.server_close()


def _post(app, path, data, origin=None):
    req = urllib.request.Request(app.origin + path, urllib.parse.urlencode(data).encode(), method="POST")
    if origin:
        req.add_header("Origin", origin)
    return urllib.request.urlopen(req, timeout=60)


def test_user_config_is_copied_once(tmp_path):
    path = ensure_user_config(tmp_path)
    path.write_text(path.read_text() + "\n# eigen wijziging\n")
    assert "eigen wijziging" in ensure_user_config(tmp_path).read_text()


def test_home_page_and_simulation_flow(server):
    home = urllib.request.urlopen(server.origin + "/").read().decode()
    assert "Simulatie starten" in home and server.token in home
    resp = _post(server, "/simuleer", {"token": server.token, "start": "2026-06-15", "days": "1", "compare": "on"},
                 origin=server.origin)
    page = resp.read().decode()
    assert "/rapport/" in resp.url
    assert "Vermogen" in page and "Zonder EMS" in page and "Laatste EMS-beslissingen" in page
    run_id = resp.url.rsplit("/", 1)[1]
    csv = urllib.request.urlopen(f"{server.origin}/export/{run_id}.csv").read().decode()
    assert csv.startswith("timestamp,local_time,")


@pytest.mark.parametrize("data, origin", [
    ({"start": "2026-06-15", "days": "1"}, None),                         # no token
    ({"token": "wrong", "start": "2026-06-15", "days": "1"}, None),       # bad token
    ({"start": "2026-06-15", "days": "1"}, "https://evil.example"),       # cross-site
])
def test_forged_requests_are_refused(server, data, origin):
    if origin:
        data = {**data, "token": server.token}
    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(server, "/simuleer", data, origin)
    assert exc.value.code == 403
    assert not server.runs


def test_invalid_input_shows_error(server):
    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(server, "/simuleer", {"token": server.token, "start": "2026-06-15", "days": "999"})
    assert exc.value.code == 400
    assert "ongeldige periode" in exc.value.read().decode()


def test_report_escapes_user_text(tmp_path):
    cfg = ensure_user_config(tmp_path)
    cfg.write_text(cfg.read_text().replace("name: Woning", "name: \"<script>x</script>\""))
    app = DesktopApp(cfg, tmp_path)
    run = app.simulate(date(2026, 3, 1), 1, compare=False)
    assert "<script>x</script>" not in run.html and "&lt;script&gt;" in run.html


def test_selftest_entrypoint(tmp_path, capsys):
    assert main(["--selftest", "--data-dir", str(tmp_path)]) == 0
    assert "SELFTEST OK" in capsys.readouterr().out
    assert (tmp_path / "energy-manager.log").exists()
