"""`python -m ems` / `ems` — command line entry point.

  ems serve [--data-dir DIR] [--host 0.0.0.0] [--port 8080] [--demo]
  ems create-user NAME --role admin
  ems backup OUT.zip
  ems check-config FILE
"""

from __future__ import annotations

import argparse
import contextlib
import getpass
import os
import sys
from pathlib import Path


def _data_dir(arg: str | None) -> Path:
    return Path(arg or os.environ.get("EMS_DATA_DIR") or "data")


def _port_free(host: str, port: int) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        if os.name != "nt":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host if host not in ("", "0.0.0.0") else "0.0.0.0", port))
        except OSError:
            return False
    return True


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ems", description="Energy Manager")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="start de EMS-server (engine + API + webinterface)")
    s.add_argument("--data-dir")
    s.add_argument("--host", default=os.environ.get("EMS_HOST", "0.0.0.0"))
    s.add_argument("--port", type=int, default=int(os.environ.get("EMS_PORT", "8080")))
    s.add_argument("--demo", action="store_true", help="Demo Mode: complete gesimuleerde woning")
    s.add_argument("--tls-cert")
    s.add_argument("--tls-key")
    s.add_argument("--log-level", default=os.environ.get("EMS_LOG_LEVEL", "info"))
    s.add_argument("--local-control-file", help="schrijf een token waarmee lokaal (127.0.0.1) gestopt kan worden")
    u = sub.add_parser("create-user", help="gebruiker aanmaken")
    u.add_argument("username")
    u.add_argument("--role", default="admin", choices=["viewer", "operator", "admin", "installer"])
    u.add_argument("--data-dir")
    b = sub.add_parser("backup", help="volledige back-up maken")
    b.add_argument("output")
    b.add_argument("--data-dir")
    b.add_argument("--include-keys", action="store_true",
                   help="ook de sleutel van de geheimenopslag meenemen (vraagt een wachtwoord; bestand wordt versleuteld)")
    c = sub.add_parser("check-config", help="configuratiebestand valideren")
    c.add_argument("file")
    sub.add_parser("selftest", help="volledige rooktest in Demo Mode (engine, optimizer, database, API, webinterface)")
    args = p.parse_args(argv)

    if args.cmd == "selftest":
        return _selftest()

    if args.cmd == "serve":
        import uvicorn

        from ems.api.app import create_app
        from ems.core.logging import setup_logging
        from ems.server.runtime import EMSRuntime

        setup_logging(args.log_level.upper(), json_output=os.environ.get("EMS_LOG_JSON", "1") == "1")
        runtime = EMSRuntime(_data_dir(args.data_dir), mode="demo" if args.demo else None)
        if not _port_free(args.host, args.port):
            print(f"Poort {args.port} is al in gebruik — draait Energy Manager al? (http://127.0.0.1:{args.port})",
                  file=sys.stderr)
            return 3
        token = None
        if args.local_control_file:
            import secrets

            token = secrets.token_urlsafe(32)
            ctl = Path(args.local_control_file)
            ctl.parent.mkdir(parents=True, exist_ok=True)
            ctl.write_text(f"{args.port}\n{token}\n", encoding="utf-8")
            with contextlib.suppress(OSError):
                os.chmod(ctl, 0o600)
        app = create_app(runtime, local_control_token=token)
        server = uvicorn.Server(uvicorn.Config(app, host=args.host, port=args.port, log_level=args.log_level.lower(),
                                               ssl_certfile=args.tls_cert, ssl_keyfile=args.tls_key,
                                               proxy_headers=False))
        app.state.request_shutdown = lambda: setattr(server, "should_exit", True)
        try:
            server.run()
        finally:
            if args.local_control_file:
                with contextlib.suppress(OSError):
                    Path(args.local_control_file).unlink()
        return 0 if server.started else 1
    if args.cmd == "create-user":
        from ems.database import Database
        from ems.security.auth import hash_password

        d = _data_dir(args.data_dir)
        db = Database(os.environ.get("EMS_DATABASE_URL") or f"sqlite:///{d / 'ems.db'}")
        db.migrate()
        pw = getpass.getpass("Wachtwoord: ")
        if len(pw) < 8:
            print("wachtwoord moet minimaal 8 tekens hebben", file=sys.stderr)
            return 1
        db.create_user(args.username, hash_password(pw), args.role)
        print(f"gebruiker {args.username} ({args.role}) aangemaakt")
        return 0
    if args.cmd == "backup":
        from ems.services.backup import create_backup

        d = _data_dir(args.data_dir)
        password = None
        if args.include_keys:
            password = getpass.getpass("Back-upwachtwoord (min. 10 tekens; nodig bij herstellen): ")
            if password != getpass.getpass("Herhaal wachtwoord: "):
                print("wachtwoorden komen niet overeen", file=sys.stderr)
                return 1
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        try:
            blob = create_backup(d, os.environ.get("EMS_DATABASE_URL") or f"sqlite:///{d / 'ems.db'}",
                                 include_keys=args.include_keys, password=password)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        Path(args.output).write_bytes(blob)
        print(f"back-up geschreven naar {args.output}")
        return 0
    if args.cmd == "check-config":
        from ems.core.config import ConfigError, load_config

        try:
            cfg = load_config(args.file)
        except ConfigError as exc:
            print(f"FOUT: {exc}", file=sys.stderr)
            return 1
        print(f"OK: {cfg.site.name}, {len(cfg.devices)} apparaten, modus {cfg.runtime.mode}")
        return 0
    return 1


def _selftest() -> int:
    import asyncio
    import tempfile

    import httpx

    from ems.api.app import WEB_DIR, create_app
    from ems.server.runtime import EMSRuntime

    async def run() -> list[str]:
        problems = []
        with tempfile.TemporaryDirectory() as tmp:
            rt = EMSRuntime(Path(tmp), mode="demo", env={})
            await rt.start(loops=False)
            try:
                for _ in range(3):
                    await rt.tick_once()
                plan = await rt.optimizer.run(rt.engine.last_snapshot, rt.now(), "selftest")
                if not plan.ok:
                    problems.append(f"optimizer: {plan.status} {plan.message}")
                app = create_app(rt, start_runtime=False)
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
                    tok = (await c.post("/api/v1/auth/login", json={"username": "demo", "password": "demo"})).json()["token"]
                    c.headers["Authorization"] = f"Bearer {tok}"
                    for path in ("/api/v1/energy/live", "/api/v1/devices", "/api/v1/prices", "/api/v1/optimizer/plan",
                                 "/api/v1/system/status", "/"):
                        r = await c.get(path)
                        if r.status_code != 200:
                            problems.append(f"{path}: HTTP {r.status_code}")
                if not (WEB_DIR / "js" / "app.js").exists():
                    problems.append("webinterface ontbreekt")
                from ems.devices.registry import registry
                ids = {d.manifest.driver_id for d in registry.list()}
                for needed in ("homewizard.p1", "dsmr.p1", "mock.battery"):
                    if needed not in ids:
                        problems.append(f"driver {needed} niet geladen")
            finally:
                await rt.stop()
        return problems

    problems = asyncio.run(run())
    if problems:
        print("SELFTEST FAILED\n" + "\n".join(problems))
        return 1
    print("SELFTEST OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
