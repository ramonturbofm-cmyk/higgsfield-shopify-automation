"""Energy Manager desktop launcher.

Starts a small web server on 127.0.0.1 and opens the browser. From there the
user runs simulations of the configured house and gets an interactive report.
Closing the console window stops the program.

Security: bound to localhost only; every POST must carry the per-session
token and a same-origin Origin header, so other websites cannot drive it.
"""

from __future__ import annotations

import argparse
import asyncio
import html
import logging
import os
import secrets
import shutil
import socket
import sys
import threading
import uuid
import webbrowser
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from ems import __version__
from ems.control.base import NativeController
from ems.control.self_consumption import SelfConsumptionController
from ems.core.config import ConfigError, EMSConfig, load_config
from ems.report import CSS, render_report
from ems.simulator.runner import SimulationResult, SimulationRunner

log = logging.getLogger("ems.desktop")
DAY_CHOICES = (1, 2, 7, 14, 30)
MAX_STORED_RUNS = 10


def resource_dir() -> Path:
    """Folder with bundled files (PyInstaller) or the source checkout."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parents[3]


def default_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "EnergyManager"


def ensure_user_config(data_dir: Path) -> Path:
    """Copy the bundled example config to the user's folder on first start."""
    data_dir.mkdir(parents=True, exist_ok=True)
    target = data_dir / "ems.yaml"
    if not target.exists():
        shutil.copyfile(resource_dir() / "config" / "ems.example.yaml", target)
    return target


@dataclass
class Run:
    id: str
    created: datetime
    label: str
    html: str
    csv_path: Path


class DesktopApp:
    def __init__(self, config_path: Path, data_dir: Path) -> None:
        self.config_path = config_path
        self.data_dir = data_dir
        self.token = secrets.token_urlsafe(24)
        self.runs: dict[str, Run] = {}
        self.lock = threading.Lock()
        self.origin = ""  # set once the server port is known

    def load_config(self) -> EMSConfig:
        cfg = load_config(self.config_path)
        # The launcher only ever drives simulated devices.
        cfg.runtime.simulation_mode = True
        return cfg

    def simulate(self, start: date, days: int, compare: bool, seed: int = 1) -> Run:
        cfg = self.load_config()
        tz = ZoneInfo(cfg.site.timezone)
        begin = datetime(start.year, start.month, start.day, tzinfo=tz)
        step = 10.0 if days <= 2 else 30.0 if days <= 7 else 60.0

        async def go() -> list[SimulationResult]:
            out = []
            for ctrl in [SelfConsumptionController()] + ([NativeController()] if compare else []):
                runner = SimulationRunner(cfg, begin, controller=ctrl, seed=seed, step_s=step)
                out.append(await runner.run(timedelta(days=days)))
            return out

        with self.lock:  # one simulation at a time
            results = asyncio.run(go())
        run_id = uuid.uuid4().hex[:10]
        csv_path = self.data_dir / "runs" / f"{run_id}.csv"
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        results[0].write_csv(csv_path)
        label = f"{begin:%d-%m-%Y}, {days} dag{'en' if days > 1 else ''}" + (" (vergeleken)" if compare else "")
        page = render_report(results, tz, title=f"Simulatie {cfg.site.name}", csv_href=f"/export/{run_id}.csv")
        page = page.replace("<header>", '<p><a href="/">← Terug</a></p><header>', 1)
        run = Run(run_id, datetime.now(), label, page, csv_path)
        self.runs[run_id] = run
        for old in sorted(self.runs.values(), key=lambda r: r.created)[:-MAX_STORED_RUNS]:
            self.runs.pop(old.id, None)
        return run

    # ----------------------------------------------------------------- pages
    def home_page(self, error: str | None = None) -> str:
        esc = html.escape
        try:
            cfg = self.load_config()
            site = f"{cfg.site.name} — {len(cfg.devices)} apparaten"
            devices = "".join(f"<li>{esc(d.name)} <small>({esc(d.category.value)})</small></li>" for d in cfg.devices)
        except (ConfigError, OSError) as exc:
            site, devices = "Configuratie bevat een fout", ""
            error = error or str(exc)
        options = "".join(f'<option value="{d}"{" selected" if d == 2 else ""}>{d} dag{"en" if d > 1 else ""}</option>'
                          for d in DAY_CHOICES)
        runs = "".join(f'<li><a href="/rapport/{r.id}">{esc(r.label)}</a> <small>{r.created:%H:%M}</small></li>'
                       for r in sorted(self.runs.values(), key=lambda r: r.created, reverse=True))
        err = f'<p class="err">{esc(error)}</p>' if error else ""
        return f"""<!doctype html><html lang="nl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Energy Manager</title>
<style>{CSS}{FORM_CSS}</style></head><body><main class="viz-root">
<header><h1>Energy Manager</h1><p class="sub">Versie {__version__} · simulatiemodus</p></header>
{err}
<section class="card"><h2>Simulatie starten</h2>
<form method="post" action="/simuleer" onsubmit="this.querySelector('button').disabled=true;
this.querySelector('button').textContent='Bezig met rekenen…'">
<input type="hidden" name="token" value="{self.token}">
<label>Startdatum <input type="date" name="start" value="{date.today():%Y-%m-%d}" required></label>
<label>Periode <select name="days">{options}</select></label>
<label class="check"><input type="checkbox" name="compare" checked> Vergelijk met "zonder EMS"</label>
<button type="submit">Simuleren</button></form></section>
{f'<section class="card"><h2>Eerdere simulaties</h2><ul>{runs}</ul></section>' if runs else ''}
<section class="card"><h2>Woning</h2><p>{esc(site)}</p><ul class="cols">{devices}</ul>
<p class="note">Instellingen aanpassen: bewerk <code>{esc(str(self.config_path))}</code> en start een nieuwe simulatie.</p>
</section>
<form method="post" action="/stop"><input type="hidden" name="token" value="{self.token}">
<button class="secondary" type="submit">Energy Manager afsluiten</button></form>
<p class="note">Dit is fase 1: een gesimuleerde woning. Koppeling met echte apparaten, het live dashboard en de
installatiewizard volgen in volgende fases.</p>
</main></body></html>"""


FORM_CSS = """
.card{background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:4px 18px 16px;margin:16px 0}
.card h2{margin:14px 0 12px}
form{display:flex;flex-wrap:wrap;gap:14px;align-items:end}label{display:flex;flex-direction:column;gap:4px;
color:var(--text2);font-size:13px}label.check{flex-direction:row;align-items:center;gap:8px;font-size:15px;color:var(--text)}
input,select{font:inherit;padding:7px 9px;border:1px solid var(--border);border-radius:8px;background:var(--surface);
color:var(--text)}button{font:inherit;font-weight:600;padding:9px 18px;border-radius:8px;border:0;background:var(--s1);
color:#fff;cursor:pointer}button:disabled{opacity:.6;cursor:wait}button.secondary{background:transparent;color:var(--text2);
border:1px solid var(--border);font-weight:500}.err{background:#fde8e7;color:#8a1c1b;padding:10px 14px;border-radius:8px}
.cols{columns:2;padding-left:18px}code{font-size:13px;word-break:break-all}
"""


class Handler(BaseHTTPRequestHandler):
    app: DesktopApp
    server_version = "EnergyManager"

    def log_message(self, fmt: str, *args) -> None:  # route to logging, not stderr
        log.debug("http: " + fmt, *args)

    def _send(self, status: int, body: str | bytes, ctype: str = "text/html; charset=utf-8",
              extra: dict[str, str] | None = None) -> None:
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy",
                         "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
                         "form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, location: str) -> None:
        self._send(303, "", extra={"Location": location})

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            return self._send(200, self.app.home_page())
        if path.startswith("/rapport/"):
            run = self.app.runs.get(path.removeprefix("/rapport/"))
            return self._send(200, run.html) if run else self._redirect("/")
        if path.startswith("/export/") and path.endswith(".csv"):
            run = self.app.runs.get(path.removeprefix("/export/").removesuffix(".csv"))
            if run and run.csv_path.exists():
                return self._send(200, run.csv_path.read_bytes(), "text/csv; charset=utf-8",
                                  {"Content-Disposition": f'attachment; filename="simulatie-{run.id}.csv"'})
        self._send(404, "Niet gevonden", "text/plain; charset=utf-8")

    def do_POST(self) -> None:
        length = min(int(self.headers.get("Content-Length") or 0), 10_000)
        form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode("utf-8", "replace")).items()}
        origin = self.headers.get("Origin")
        if form.get("token") != self.app.token or (origin and origin != self.app.origin):
            return self._send(403, "Verzoek geweigerd", "text/plain; charset=utf-8")
        path = urlparse(self.path).path
        if path == "/stop":
            self._send(200, "<!doctype html><meta charset=utf-8><title>Afgesloten</title>"
                            "<p style='font-family:sans-serif'>Energy Manager is afgesloten. "
                            "U kunt dit venster sluiten.</p>")
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return None
        if path == "/simuleer":
            try:
                start = date.fromisoformat(form.get("start", ""))
                days = int(form.get("days", "2"))
                if days not in DAY_CHOICES:
                    raise ValueError("ongeldige periode")
                run = self.app.simulate(start, days, compare="compare" in form)
            except (ValueError, ConfigError) as exc:
                return self._send(400, self.app.home_page(error=f"Simulatie mislukt: {exc}"))
            except Exception as exc:  # show, don't crash the server
                log.exception("simulation failed")
                return self._send(500, self.app.home_page(error=f"Onverwachte fout: {exc}"))
            return self._redirect(f"/rapport/{run.id}")
        self._send(404, "Niet gevonden", "text/plain; charset=utf-8")


def make_server(app: DesktopApp, port: int = 0) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"app": app})
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    app.origin = f"http://127.0.0.1:{server.server_address[1]}"
    return server


def _free_port(preferred: int) -> int:
    with socket.socket() as s:
        if os.name != "nt":  # match ThreadingHTTPServer.allow_reuse_address (TIME_WAIT after restart)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            return 0


def self_test(data_dir: Path) -> int:
    """Headless check used by the build pipeline: config + short simulation + report."""
    app = DesktopApp(ensure_user_config(data_dir), data_dir)
    run = app.simulate(date(2026, 6, 15), 1, compare=True)
    ok = "Vermogen" in run.html and run.csv_path.stat().st_size > 0
    print("SELFTEST", "OK" if ok else "FAILED", run.csv_path)
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="EnergyManager")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--data-dir", type=Path, default=default_data_dir())
    p.add_argument("--no-browser", action="store_true")
    p.add_argument("--selftest", action="store_true", help="korte simulatie zonder browser; exitcode 0 = OK")
    args = p.parse_args(argv)

    args.data_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(args.data_dir / "energy-manager.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    if args.selftest:
        return self_test(args.data_dir)

    app = DesktopApp(ensure_user_config(args.data_dir), args.data_dir)
    server = make_server(app, _free_port(args.port))
    url = app.origin + "/"
    print(f"Energy Manager {__version__} draait op {url}")
    print("Sluit dit venster (of klik 'Energy Manager afsluiten' in de browser) om te stoppen.")
    print(f"Instellingen: {app.config_path}")
    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
