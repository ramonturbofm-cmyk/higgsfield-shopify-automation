"""Entry point of the EMS server on a Windows PC (all-in-one installation, no Raspberry Pi).

Two executables are built from this file (same files, see EnergyManagerServer.spec):

  EnergyManagerService.exe   no window: started by the app and at Windows sign-in
  EnergyManagerServer.exe    with a console window (troubleshooting)

Options:
  --demo / --production   choose the mode; remembered in %LOCALAPPDATA%\\EnergyManager\\mode.txt
  --background            no browser, output to %LOCALAPPDATA%\\EnergyManager\\logs\\server.log
  --stop                  stop a running server gracefully (devices are released first)
  --lan                   also reachable from phones/tablets on the network (default: this PC only)
  --no-browser            do not open the browser
  --selftest              build check

The PC must stay on for the EMS to keep working; it runs on in the background when the
app window is closed.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

PORT = 8080


def root() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "EnergyManager"


def control_file() -> Path:
    return root() / "control.txt"


def mode_file() -> Path:
    return root() / "mode.txt"


def choose_mode(args: list[str]) -> str:
    if "--demo" in args or "--production" in args:
        mode = "demo" if "--demo" in args else "production"
        root().mkdir(parents=True, exist_ok=True)
        mode_file().write_text(mode, encoding="utf-8")
        return mode
    try:
        saved = mode_file().read_text(encoding="utf-8").strip()
    except OSError:
        saved = ""
    return saved if saved in ("demo", "production") else "production"


def port_open(port: int = PORT) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def stop() -> int:
    try:
        port, token = control_file().read_text(encoding="utf-8").split()[:2]
    except (OSError, ValueError):
        print("Energy Manager draait niet (geen besturingsbestand).")
        return 0
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/v1/system/local-shutdown", data=b"",
                                 method="POST", headers={"x-ems-local-token": token})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            json.loads(r.read() or b"{}")
    except OSError as exc:
        if not port_open(int(port)):
            print("Energy Manager draait niet.")
            return 0
        print(f"Stoppen mislukt: {exc}")
        return 1
    for _ in range(60):
        if not port_open(int(port)):
            print("Energy Manager is gestopt.")
            return 0
        time.sleep(0.5)
    print("Energy Manager reageert niet op stoppen.")
    return 1


def redirect_output() -> None:
    logs = root() / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    log = logs / "server.log"
    if log.exists() and log.stat().st_size > 5_000_000:
        log.replace(logs / "server.log.1")
    stream = open(log, "a", encoding="utf-8", buffering=1)  # noqa: SIM115 - lives as long as the process
    sys.stdout = sys.stderr = stream
    print(f"\n==== {time.strftime('%Y-%m-%d %H:%M:%S')} start ====")


def run(args: list[str]) -> int:
    from ems.__main__ import main

    if "--selftest" in args:
        return main(["selftest"])
    if "--stop" in args:
        return stop()
    background = "--background" in args or sys.stdout is None
    if background:
        redirect_output()
    mode = choose_mode(args)
    data_dir = root() / ("demo" if mode == "demo" else "server")
    host = "0.0.0.0" if "--lan" in args else "127.0.0.1"
    print(f"Energy Manager server ({'Demo Mode' if mode == 'demo' else 'eigen installatie'}) — http://127.0.0.1:{PORT}")
    print(f"Gegevens: {data_dir}")
    if not background:
        print("Sluit dit venster om de server te stoppen. Apparaten vallen dan terug op hun eigen regeling.")
        if "--no-browser" not in args:
            threading.Timer(4.0, webbrowser.open, args=(f"http://127.0.0.1:{PORT}/",)).start()
    os.environ.setdefault("EMS_LOG_JSON", "0")
    cli = ["serve", "--data-dir", str(data_dir), "--host", host, "--port", str(PORT), "--log-level", "warning",
           "--local-control-file", str(control_file())]
    return main(cli + (["--demo"] if mode == "demo" else []))


def _pause(message: str) -> None:
    # The console window closes as soon as the process ends; keep errors readable.
    print(message)
    try:
        input("Druk op Enter om dit venster te sluiten…")
    except (EOFError, KeyboardInterrupt, RuntimeError):
        pass


if __name__ == "__main__":
    argv = sys.argv[1:]
    try:
        code = run(argv)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
    except BaseException:
        import traceback

        traceback.print_exc()
        code = 1
    interactive = sys.stdin is not None and sys.stdout is sys.__stdout__ and sys.__stdout__ is not None
    if code == 3 and interactive:
        _pause(f"\nEnergy Manager draait al op de achtergrond: open http://127.0.0.1:{PORT} of de app.")
    elif code and interactive and not ({"--selftest", "--stop"} & set(argv)):
        _pause(f"\nDe server is gestopt met foutcode {code}. Maak een screenshot van dit venster.")
    sys.exit(code)
