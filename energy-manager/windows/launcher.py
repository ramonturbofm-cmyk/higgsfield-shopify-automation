"""Entry point for the packaged EnergyManager.exe (PyInstaller)."""

import sys

from ems.desktop.app import main

if __name__ == "__main__":
    sys.exit(main())
