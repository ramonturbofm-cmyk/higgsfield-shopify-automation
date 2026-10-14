"""Build identity and cache control: after an update the web interface can never mix old JavaScript with a new
server (the 0.6.0 'old wizard after upgrade' bug: static files were cached heuristically by WebView2)."""

import re

import httpx
import pytest

from ems import __version__, buildinfo
from ems.api.app import create_app
from ems.server.runtime import EMSRuntime


@pytest.fixture
async def client(tmp_path):
    rt = EMSRuntime(tmp_path / "data", mode="demo", env={})
    await rt.start(loops=False)
    app = create_app(rt, start_runtime=False)
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    yield c
    await c.aclose()
    await rt.stop()


async def test_index_names_versioned_assets_and_is_never_cached(client):
    b = buildinfo.info()
    r = await client.get("/")
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["x-ems-version"] == __version__ and r.headers["x-ems-build"] == b["build"]
    html = r.text
    assert f'/static/v/{b["asset_hash"]}/js/app.js' in html and f'/static/v/{b["asset_hash"]}/css/app.css' in html
    assert '"/static/js/app.js"' not in html
    assert f'<meta name="ems-asset" content="{b["asset_hash"]}">' in html
    assert f'<meta name="ems-version" content="{__version__}">' in html


async def test_versioned_assets_immutable_old_versions_not_cached(client):
    h = buildinfo.asset_hash()
    r = await client.get(f"/static/v/{h}/js/views/devices.js")
    assert r.status_code == 200 and "immutable" in r.headers["cache-control"]
    assert 'from "./addwizard.js"' in r.text                       # the new wizard is what is served
    w = await client.get(f"/static/v/{h}/js/views/addwizard.js")
    assert "Kies merk en model" in w.text and "Via een gateway" in w.text and "Handmatig (Expert)" in w.text
    old = await client.get("/static/v/0123456789abcdef/js/app.js")  # page from before an update
    assert old.status_code == 200 and old.headers["cache-control"] == "no-store"
    plain = await client.get("/static/js/app.js")
    assert plain.headers["cache-control"] == "no-cache"             # always revalidated
    assert (await client.get(f"/static/v/{h}/../../ems.yaml")).status_code == 404


async def test_relative_imports_only(client):
    """Modules must import each other relatively, so the version in the URL carries over to every module."""
    from ems.buildinfo import WEB_DIR
    for f in (WEB_DIR / "js").rglob("*.js"):
        src = f.read_text(encoding="utf-8")
        for spec in re.findall(r'(?:import|from)\s*\(?\s*"([^"]+\.js)"', src):
            assert spec.startswith("."), f"{f.name}: absolute import {spec}"


async def test_health_and_info_report_build(client):
    hz = (await client.get("/healthz")).json()
    assert hz["version"] == __version__ and hz["asset_hash"] == buildinfo.asset_hash() and hz["build"]
    info = (await client.get("/api/v1/system/info")).json()
    assert info["build"]["asset_hash"] == hz["asset_hash"] and info["build"]["consistent"]
    r = await client.get("/api/v1/system/info")
    assert r.headers["cache-control"] == "no-store"


def test_asset_hash_changes_with_any_web_file(tmp_path):
    from ems.buildinfo import _web_hash
    (tmp_path / "js").mkdir()
    (tmp_path / "js" / "a.js").write_text("1")
    h1 = _web_hash(tmp_path)
    (tmp_path / "js" / "a.js").write_text("2")
    assert _web_hash(tmp_path) != h1
