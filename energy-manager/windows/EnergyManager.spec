# PyInstaller spec — run from the energy-manager directory:  pyinstaller windows/EnergyManager.spec
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

hiddenimports = collect_submodules("ems")          # drivers are discovered dynamically
datas = [("../config/ems.example.yaml", "config")] + collect_data_files("tzdata")

a = Analysis(
    ["launcher.py"],
    pathex=["../backend"],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "pytest", "numpy", "scipy"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="EnergyManager",
    console=True,             # the console window is the "running" indicator; closing it stops the app
    upx=False,                # UPX-packed executables trigger more antivirus false positives
)
