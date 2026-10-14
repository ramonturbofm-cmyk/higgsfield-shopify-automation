# PyInstaller spec for the Windows EMS server. Run from energy-manager/:
#   pyinstaller --noconfirm windows/EnergyManagerServer.spec
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

hiddenimports = (collect_submodules("ems") + collect_submodules("uvicorn") + collect_submodules("websockets")
                 + collect_submodules("pymodbus") + collect_submodules("serial")
                 + ["zeroconf._utils.ipaddress", "zeroconf._handlers.answers", "aiomqtt", "paho.mqtt.client"])
import os

datas = collect_data_files("ems", include_py_files=False) + collect_data_files("tzdata")
# Build identity written by tools/write_build_info.py just before this build (the installed package may predate it).
_build_info = os.path.join(SPECPATH, "..", "backend", "ems", "BUILD_INFO.json")
if os.path.exists(_build_info):
    datas = [d for d in datas if not d[0].endswith("BUILD_INFO.json")] + [(_build_info, "ems")]

a = Analysis(["server_launcher.py"], pathex=["../backend"], datas=datas, hiddenimports=hiddenimports,
             excludes=["tkinter", "pytest", "matplotlib", "IPython"])
pyz = PYZ(a.pure)
# Same program twice: with a console window (troubleshooting) and without (background service
# started by the app and at Windows sign-in). Both share the files in one folder.
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="EnergyManagerServer", console=True, upx=False)
svc = EXE(pyz, a.scripts, [], exclude_binaries=True, name="EnergyManagerService", console=False, upx=False)
coll = COLLECT(exe, svc, a.binaries, a.datas, name="EnergyManagerServer", upx=False)
