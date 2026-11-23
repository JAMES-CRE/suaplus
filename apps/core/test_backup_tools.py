"""Verify backup behavior with local command fakes, never real database writes."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


@pytest.mark.skipif(
    shutil.which("bash") is None, reason="Bash required for operator scripts"
)
def test_backup_propagates_failure_and_does_not_prune(tmp_path):
    tools = tmp_path / "tools"
    tools.mkdir()
    dump = tools / "pg_dump"
    dump.write_text("#!/bin/sh\nexit 7\n", encoding="utf-8")
    dump.chmod(0o755)
    env = {
        **os.environ,
        "PATH": str(tools) + os.pathsep + os.environ["PATH"],
        "DB_NAME": "disposable",
        "DB_USER": "tester",
        "DB_HOST": "localhost",
        "DB_PASSWORD": "test-only",
        "BACKUP_DIR": str(tmp_path / "backups"),
    }
    result = subprocess.run(
        ["bash", str(SCRIPTS / "backup_db.sh")],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "BACKUP_OK" not in result.stdout
    assert not list((tmp_path / "backups").glob("*.dump"))


@pytest.mark.skipif(
    shutil.which("bash") is None, reason="Bash required for operator scripts"
)
def test_restore_refuses_application_database(tmp_path):
    env = {
        **os.environ,
        "DB_NAME": "live",
        "RESTORE_DB_NAME": "live",
        "CONFIRM_RESTORE": "live",
        "DB_USER": "tester",
        "DB_HOST": "localhost",
    }
    result = subprocess.run(
        ["bash", str(SCRIPTS / "restore_db.sh"), str(tmp_path / "missing.dump")],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "Refusing restore" in result.stderr
