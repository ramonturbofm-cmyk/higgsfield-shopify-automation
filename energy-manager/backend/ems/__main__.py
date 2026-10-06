"""`python -m ems` / `ems` — command line entry point.

  ems serve [--data-dir DIR] [--host 0.0.0.0] [--port 8080] [--demo]
  ems create-user NAME --role admin
  ems backup OUT.zip
  ems check-config FILE
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path


def _data_dir(arg: str | None) -> Path:
    return Path(arg or os.environ.get("EMS_DATA_DIR") or "data")


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
    u = sub.add_parser("create-user", help="gebruiker aanmaken")
    u.add_argument("username")
    u.add_argument("--role", default="admin", choices=["viewer", "operator", "admin", "installer"])
    u.add_argument("--data-dir")
    b = sub.add_parser("backup", help="volledige back-up maken")
    b.add_argument("output")
    b.add_argument("--data-dir")
    c = sub.add_parser("check-config", help="configuratiebestand valideren")
    c.add_argument("file")
    args = p.parse_args(argv)

    if args.cmd == "serve":
        import uvicorn

        from ems.api.app import create_app
        from ems.core.logging import setup_logging
        from ems.server.runtime import EMSRuntime

        setup_logging(args.log_level.upper(), json_output=os.environ.get("EMS_LOG_JSON", "1") == "1")
        runtime = EMSRuntime(_data_dir(args.data_dir), mode="demo" if args.demo else None)
        app = create_app(runtime)
        uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level.lower(),
                    ssl_certfile=args.tls_cert, ssl_keyfile=args.tls_key, proxy_headers=False)
        return 0
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
        Path(args.output).write_bytes(create_backup(d, os.environ.get("EMS_DATABASE_URL")
                                                    or f"sqlite:///{d / 'ems.db'}"))
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


if __name__ == "__main__":
    sys.exit(main())
