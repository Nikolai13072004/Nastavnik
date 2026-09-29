"""Real age/rclone crypt, fake Docker only. All keys and .env are synthetic.

Linux operational gate: requires age, age-keygen, rclone, Bash and flock.
The crypt destination is local, NOT a test of an external storage provider.
"""
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from scripts.backup_artifacts import verify


TOOLS = ("age", "age-keygen", "rclone", "bash", "flock")
pytestmark = pytest.mark.skipif(
    os.name == "nt" or not all(shutil.which(tool) for tool in TOOLS),
    reason="isolated Linux operational gate requires age/rclone/Bash/flock",
)
SYNTHETIC = b"JWT_SECRET_KEY=synthetic-recovery-key-not-for-production\n"


def run(args, **kwargs):
    return subprocess.run(args, capture_output=True, timeout=30, **kwargs)


@pytest.fixture
def secret_drill(tmp_path):
    root = Path(__file__).resolve().parents[1]
    app = tmp_path / "app"
    scripts = app / "scripts"
    scripts.mkdir(parents=True)
    for name in ("backup.sh", "backup_artifacts.py"):
        # Normalize checkout CRLF for Bash while leaving the repo untouched.
        (scripts / name).write_text((root / "scripts" / name).read_text())
    (app / ".env").write_bytes(SYNTHETIC)
    backups = app / "backups"
    keys = tmp_path / "operator-keys"
    keys.mkdir()
    identity = keys / "identity.txt"
    assert run(["age-keygen", "-o", str(identity)]).returncode == 0
    public = run(["age-keygen", "-y", str(identity)])
    assert public.returncode == 0
    bindir = tmp_path / "bin"
    bindir.mkdir()
    docker = bindir / "docker"
    docker.write_text('''#!/usr/bin/env python3
import io, os, pathlib, sys, tarfile
a = sys.argv[1:]
with open(os.environ['CALL_LOG'], 'a') as log: log.write(' '.join(a) + '\\n')
if a[:3] == ['compose', 'ps', '-q']: print('synthetic-container')
elif a[0] == 'inspect': print('0')
elif a[:2] == ['compose', 'exec']: print('SELECT 1;')
elif a[0] == 'run':
    # The real script streams `docker run --volumes-from … tar czf - …`
    # to the host; model that stdout archive rather than the retired -v mount.
    with tarfile.open(fileobj=sys.stdout.buffer, mode='w:gz') as archive:
        for folder in ['app/data', 'app/docs']:
            entry = tarfile.TarInfo(folder); entry.type = tarfile.DIRTYPE
            archive.addfile(entry)
''')
    docker.chmod(0o700)
    remote = tmp_path / "encrypted-remote"
    remote.mkdir()
    password = run(["rclone", "obscure", "synthetic-crypt-password"])
    assert password.returncode == 0
    rclone_config = tmp_path / "rclone.conf"
    rclone_config.write_text(
        f"[drill]\ntype = crypt\nremote = {remote}\n"
        f"password = {password.stdout.decode().strip()}\nfilename_encryption = standard\n"
    )
    env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}",
               COMPOSE="docker compose", BACKUP_DIR=str(backups),
               BACKUP_SECRETS_RECIPIENT=public.stdout.decode().strip(),
               OFFSITE_REMOTE="drill:", RCLONE_CONFIG=str(rclone_config),
               CALL_LOG=str(tmp_path / "calls"), PAUSE_FOR_CONSISTENCY="true", RETENTION="14")
    return app, backups, identity, remote, env


def backup(drill, **overrides):
    app, _, _, _, env = drill
    return run(["bash", str(app / "scripts/backup.sh")], env=dict(env, **overrides))


def test_encrypted_secret_and_crypt_destination_roundtrip(secret_drill, tmp_path):
    _, backups, identity, remote, env = secret_drill
    result = backup(secret_drill)
    assert result.returncode == 0, result.stderr.decode()
    assert SYNTHETIC.strip() not in result.stdout + result.stderr
    encrypted = next(backups.glob("env-*.age"))
    stamp = encrypted.name[4:-4]
    verify(backups, stamp)
    assert encrypted.stat().st_mode & 0o077 == 0
    assert encrypted.name in (backups / f"manifest-{stamp}.sha256").read_text()
    assert {p.suffix for p in backups.iterdir()} == {".age", ".gz", ".sha256"}
    assert SYNTHETIC not in encrypted.read_bytes()
    decrypted = run(["age", "-d", "-i", str(identity), str(encrypted)])
    assert decrypted.returncode == 0 and decrypted.stdout == SYNTHETIC

    restored = tmp_path / "downloaded"
    copied = run(["rclone", "copy", "drill:", str(restored)], env=env)
    assert copied.returncode == 0, copied.stderr.decode()
    verify(restored, stamp)
    assert (restored / encrypted.name).read_bytes() == encrypted.read_bytes()
    # Encrypted names at rest; only local fixture bytes, no external provider.
    assert not (remote / encrypted.name).exists()
    assert all(SYNTHETIC not in p.read_bytes() for p in remote.rglob("*") if p.is_file())


def test_wrong_identity_and_ciphertext_tamper_are_rejected(secret_drill, tmp_path):
    assert backup(secret_drill).returncode == 0
    _, backups, _, _, _ = secret_drill
    encrypted = next(backups.glob("env-*.age"))
    wrong = tmp_path / "wrong-identity.txt"
    assert run(["age-keygen", "-o", str(wrong)]).returncode == 0
    result = run(["age", "-d", "-i", str(wrong), str(encrypted)])
    assert result.returncode != 0 and not result.stdout
    data = encrypted.read_bytes()
    encrypted.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
    with pytest.raises(ValueError, match="checksum mismatch"):
        verify(backups, encrypted.name[4:-4])


def test_encryption_failure_does_not_publish_manifest(secret_drill):
    result = backup(secret_drill, BACKUP_SECRETS_RECIPIENT="invalid-synthetic-recipient")
    _, backups, _, remote, _ = secret_drill
    assert result.returncode != 0
    assert not list(backups.glob("manifest-*.sha256"))
    assert not list(remote.iterdir())
    assert SYNTHETIC.strip() not in result.stdout + result.stderr


def test_offsite_failure_is_nonzero_but_local_snapshot_survives(secret_drill):
    result = backup(secret_drill, OFFSITE_REMOTE="nonexistent-synthetic-remote:")
    _, backups, _, _, _ = secret_drill
    assert result.returncode != 0
    manifest = next(backups.glob("manifest-*.sha256"))
    verify(backups, manifest.name[9:-7])
    assert b"Done:" not in result.stdout
