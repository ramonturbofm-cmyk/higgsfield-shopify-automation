# PyInstaller spec for the Windows EMS server. Run from energy-manager/:
#   pyinstaller --noconfirm windows/EnergyManagerServer.spec
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

hiddenimports = (collect_submodules("ems") + collect_submodules("uvicorn") + collect_submodules("websockets")
                 + ["zeroconf._utils.ipaddress", "zeroconf._handlers.answers"])
datas = collect_data_files("ems", include_py_files=False) + collect_data_files("tzdata")

a = Analysis(["server_launcher.py"], pathex=["../backend"], datas=datas, hiddenimports=hiddenimports,
             excludes=["tkinter", "pytest", "matplotlib", "IPython"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="EnergyManagerServer", console=True, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name="EnergyManagerServer", upx=False)
