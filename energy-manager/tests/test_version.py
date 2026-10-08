"""Audit P2-12 / test 24: one version everywhere, and no downgrade onto newer data."""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_all_version_strings_match_the_single_source():
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "set_version.py"), "--check"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout
    from ems import __version__
    assert __version__ != "0.3.0"                       # a new release never reuses an old number


def test_older_program_refuses_newer_database(tmp_path):
    from ems.database import Database
    from ems.database import db as dbmod
    d = Database(f"sqlite:///{tmp_path / 'ems.db'}")
    d.migrate()
    with d.engine.begin() as c:
        from sqlalchemy import insert

        from ems.database import schema as s
        c.execute(insert(s.schema_version).values(version=dbmod.SCHEMA_VERSION + 1))
    with pytest.raises(RuntimeError, match="schemaversie"):
        d.migrate()


def test_installer_blocks_downgrade():
    iss = (ROOT / "windows" / "installer.iss").read_text(encoding="utf-8")
    assert "function InitializeSetup" in iss and "ALLOWDOWNGRADE" in iss and "DisplayVersion" in iss
