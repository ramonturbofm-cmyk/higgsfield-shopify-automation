"""Entry point of EnergyManagerServer.exe — the EMS server on a Windows PC.

Meant for trying the full software without a Raspberry Pi (Demo Mode) or as a
temporary server. The Windows PC must stay on for the EMS to keep controlling;
for 24/7 use install the server on a Raspberry Pi.

  EnergyManagerServer.exe            production server (real devices)
  EnergyManagerServer.exe --demo     complete simulated Demo Home
  EnergyManagerServer.exe --lan      also reachable from other devices on the network
  EnergyManagerServer.exe --selftest
"""

import os
import sys
import threading
import webbrowser

from ems.__main__ import main


def run() -> int:
    args = sys.argv[1:]
    if "--selftest" in args:
        return main(["selftest"])
    demo = "--demo" in args
    port = "8080"
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    data_dir = os.path.join(base, "EnergyManager", "demo" if demo else "server")
    host = "0.0.0.0" if "--lan" in args else "127.0.0.1"
    print(f"Energy Manager server ({'Demo Mode' if demo else 'productie'}) — http://127.0.0.1:{port}")
    print(f"Gegevens: {data_dir}")
    print("Sluit dit venster om de server te stoppen. Apparaten vallen dan terug op hun eigen regeling.")
    if "--no-browser" not in args:
        threading.Timer(4.0, webbrowser.open, args=(f"http://127.0.0.1:{port}/",)).start()
    os.environ.setdefault("EMS_LOG_JSON", "0")
    cli = ["serve", "--data-dir", data_dir, "--host", host, "--port", port, "--log-level", "warning"]
    return main(cli + (["--demo"] if demo else []))


if __name__ == "__main__":
    sys.exit(run())
