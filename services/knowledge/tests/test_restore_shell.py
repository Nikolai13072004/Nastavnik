"""Run actual shell restore with a fake Docker boundary (Linux/WSL/CI)."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from test_backup_artifacts import snapshot, STAMP

pytestmark = pytest.mark.skipif(os.name == "nt", reason="Bash/flock tests run on Linux/WSL")


@pytest.fixture
def shell_env(tmp_path):
    root = Path(__file__).resolve().parents[1]
    scripts = tmp_path / "scripts"; scripts.mkdir()
    for name in ["restore.sh", "backup_artifacts.py"]:
        shutil.copyfile(root / "scripts" / name, scripts / name)
    backups = tmp_path / "backups"; backups.mkdir(); snapshot(backups)
    bindir = tmp_path / "bin"; bindir.mkdir()
    log = tmp_path / "calls"
    docker = bindir / "docker"
    docker.write_text('''#!/usr/bin/env bash
echo "$*" >> "$CALL_LOG"
case "$*" in
  "compose ps -a -q backend") echo synthetic-container;;
  inspect*) printf 'volume /app/data\\nvolume /app/docs\\n';;
  "compose exec"*) cat >/dev/null; [ "${FAIL_STAGE:-}" != sql ];;
  run*) [ "${FAIL_STAGE:-}" != files ];;
esac
''')
    docker.chmod(0o700)
    env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}", COMPOSE="docker compose", FORCE="1", CALL_LOG=str(log))
    return tmp_path, env, log


@pytest.mark.parametrize("stage", ["sql", "files"])
def test_failure_never_restarts_backend(shell_env, stage):
    root, env, log = shell_env
    result = subprocess.run(["bash", str(root / "scripts/restore.sh"), STAMP], env=dict(env, FAIL_STAGE=stage), capture_output=True, text=True)
    assert result.returncode != 0
    assert "compose stop backend" in log.read_text()
    assert "start synthetic-container" not in log.read_text()
    assert "remains stopped" in result.stderr


def test_success_restarts_only_after_restore(shell_env):
    root, env, log = shell_env
    result = subprocess.run(["bash", str(root / "scripts/restore.sh"), STAMP], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    calls = log.read_text()
    assert calls.index("compose stop") < calls.index("compose exec") < calls.index("run --rm") < calls.index("start synthetic-container")


def test_missing_manifest_never_calls_docker(shell_env):
    root, env, log = shell_env
    (root / "backups" / f"manifest-{STAMP}.sha256").unlink()
    result = subprocess.run(["bash", str(root / "scripts/restore.sh"), STAMP], env=env, capture_output=True)
    assert result.returncode != 0 and not log.exists()
