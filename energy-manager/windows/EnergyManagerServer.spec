# PyInstaller spec for the Windows EMS server. Run from energy-manager/:
#   pyinstaller --noconfirm windows/EnergyManagerServer.spec
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

hiddenimports = (collect_submodules("ems") + collect_submodules("uvicorn") + collect_submodules("websockets")
                 + ["zeroconf._utils.ipaddress", "zeroconf._handlers.answers", "aiomqtt", "paho.mqtt.client"])
datas = collect_data_files("ems", include_py_files=False) + collect_data_files("tzdata")

a = Analysis(["server_launcher.py"], pathex=["../backend"], datas=datas, hiddenimports=hiddenimports,
             excludes=["tkinter", "pytest", "matplotlib", "IPython"])
pyz = PYZ(a.pure)
# Same program twice: with a console window (troubleshooting) and without (background service
# started by the app and at Windows sign-in). Both share the files in one folder.
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="EnergyManagerServer", console=True, upx=False)
svc = EXE(pyz, a.scripts, [], exclude_binaries=True, name="EnergyManagerService", console=False, upx=False)
coll = COLLECT(exe, svc, a.binaries, a.datas, name="EnergyManagerServer", upx=False)
