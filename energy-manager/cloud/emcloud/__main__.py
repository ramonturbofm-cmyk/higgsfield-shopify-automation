"""``emcloud serve`` | ``emcloud create-admin EMAIL`` | ``emcloud maintenance`` | ``emcloud gen-key``"""

from __future__ import annotations

import argparse
import getpass
import sys
import time


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="emcloud")
    sub = p.add_subparsers(dest="cmd", required=True)
    sv = sub.add_parser("serve")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8800)
    ca = sub.add_parser("create-admin", help="platform admin account (MFA required at first login)")
    ca.add_argument("email")
    sub.add_parser("maintenance")
    sub.add_parser("gen-key", help="new key for EMC_ENCRYPTION_KEYS")
    a = p.parse_args(argv)
    if a.cmd == "gen-key":
        from cryptography.fernet import Fernet
        print(Fernet.generate_key().decode())
        return 0
    from emcloud.app import create_app, maintenance
    if a.cmd == "serve":
        import uvicorn
        uvicorn.run(create_app(), host=a.host, port=a.port, proxy_headers=True, forwarded_allow_ips="127.0.0.1")
        return 0
    app = create_app(background=False)
    if a.cmd == "maintenance":
        print(maintenance(app))
        return 0
    from sqlalchemy import select

    from emcloud.db import User
    from emcloud.security import check_password_policy, hash_password
    pw = getpass.getpass("Wachtwoord: ")
    if msg := check_password_policy(pw, a.email):
        print(msg, file=sys.stderr)
        return 2
    with app.state.sessionmaker() as db:
        email = a.email.strip().lower()
        u = db.scalar(select(User).where(User.email == email))
        if u is None:
            u = User(email=email, created_ts=time.time())
            db.add(u)
        u.password_hash, u.is_platform_admin, u.email_verified_ts = hash_password(pw), True, time.time()
        db.commit()
    print(f"platformbeheerder {email} aangemaakt; stel bij de eerste login MFA in.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
