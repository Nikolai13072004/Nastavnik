"""Restore the MAX databases, originals and index into fresh local resources."""

import argparse
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy" / "max"
FILES = (
    "prodigy.dump", "vedomo.dump", "prodigy.counts", "vedomo.counts",
    "prodigy-documents.tar.gz", "vedomo-documents.tar.gz", "vedomo-data.tar.gz",
)


def run(command, env, stdin=None):
    result = subprocess.run(command, cwd=ROOT, env=env, stdin=stdin,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if result.returncode:
        for line in result.stderr.decode("utf-8", errors="replace").splitlines():
            if line.startswith("RESTORED_VEDOMO_FAILED ("):
                print(line, flush=True)
        raise RuntimeError(f"Command failed ({result.returncode}); private output withheld")
    return result.stdout.decode("utf-8").strip()


def verify_backup(path):
    if not re.fullmatch(r"full-stack-[A-Za-z0-9]+", path.name) or not path.is_dir():
        raise ValueError("Choose the captured full-stack-* directory")
    manifest = {}
    for line in (path / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        digest, filename = line.split(None, 1)
        manifest[filename.strip().removeprefix("./")] = digest
    for filename in FILES:
        file = path / filename
        assert file.is_file() and not file.is_symlink()
        assert hashlib.sha256(file.read_bytes()).hexdigest() == manifest[filename]
        if filename.endswith(".tar.gz"):
            with tarfile.open(file, "r:gz") as archive:
                for member in archive.getmembers():
                    archive_path = PurePosixPath(member.name)
                    assert not archive_path.is_absolute() and "\\" not in member.name
                    assert ".." not in archive_path.parts
                    assert member.isfile() or member.isdir(), "Links and special files are not restored"
    print("Backup checksums and archive paths verified.", flush=True)


def check_counts(compose, service, expected, env):
    query = "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
    sql_command = compose + ["exec", "-T", service, "sh", "-c",
        'exec psql -X -At -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$1"', "sh"]
    tables = run(sql_command + [query], env).splitlines()
    counts = []
    for table in tables:
        assert re.fullmatch(r"[A-Za-z0-9_]+", table)
        count = run(sql_command + [f'SELECT count(*) FROM "{table}"'], env)
        counts.append(f"{table}|{count}")
    assert counts == expected.read_text(encoding="utf-8").splitlines()
    print(f"{service}: all {len(tables)} table counts match the backup.", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backup", required=True, type=Path)
    parser.add_argument("--project", required=True)
    parser.add_argument("--prodigy-image", default="prodigy-max:cold-check-20260927")
    parser.add_argument("--vedomo-image", default="vedomo-backend:cold-check-20260927")
    parser.add_argument("--model-volume", default="vedomo-max-local-models")
    args = parser.parse_args()
    assert re.fullmatch(r"prodigy-max-review-restore-[a-z0-9-]{1,32}", args.project)
    backup = args.backup.resolve()
    verify_backup(backup)
    env = os.environ.copy()
    env.update({
        "REVIEW_PRODIGY_IMAGE": args.prodigy_image,
        "REVIEW_VEDOMO_IMAGE": args.vedomo_image,
        "REVIEW_MODEL_VOLUME": args.model_volume,
        "REVIEW_DOCUMENT_IMPORT_ENABLED": "true",
    })
    for kind in ("container", "volume", "network"):
        flags = "-aq" if kind == "container" else "-q"
        assert not run(["docker", kind, "ls", flags, "--filter",
                        f"label=com.docker.compose.project={args.project}"], env), "Use a fresh review name"
    for image in (args.prodigy_image, args.vedomo_image, "postgres:16-alpine", "nginx:stable-alpine"):
        run(["docker", "image", "inspect", image], env)
    run(["docker", "volume", "inspect", args.model_volume], env)
    compose = ["docker", "compose", "-p", args.project,
               "--env-file", str(DEPLOY / ".env.joint-review")]
    for filename in ("compose.review.yml", "compose.joint-review.yml", "compose.full-restore-review.yml"):
        compose.extend(["-f", str(DEPLOY / filename)])
    run(compose + ["config", "--quiet"], env)
    started = False
    try:
        started = True
        run(compose + ["create", "--no-build", "--pull", "never"], env)
        for filename, volume in (
            ("prodigy-documents.tar.gz", "documents"),
            ("vedomo-documents.tar.gz", "vedomo_documents"),
            ("vedomo-data.tar.gz", "vedomo_data"),
        ):
            target = f"{args.project}_{volume}"
            labels = run(["docker", "volume", "inspect", "--format", "{{json .Labels}}", target], env)
            assert args.project in labels
            run(["docker", "run", "--rm", "--network", "none", "--memory", "96m", "--cpus", "0.2",
                 "--pids-limit", "32", "--mount", f"type=bind,source={backup},target=/backup,readonly",
                 "--mount", f"source={target},target=/restore", "--entrypoint", "tar", "nginx:stable-alpine",
                 "-xzf", f"/backup/{filename}", "-C", "/restore"], env)
        run(compose + ["up", "-d", "--no-build", "--pull", "never", "--wait",
                       "--wait-timeout", "90", "db", "vedomo-db"], env)
        for service, name in (("db", "prodigy"), ("vedomo-db", "vedomo")):
            with (backup / f"{name}.dump").open("rb") as stream:
                run(compose + ["exec", "-T", service, "sh", "-c",
                    'exec pg_restore --exit-on-error --no-owner --no-acl -U "$POSTGRES_USER" -d "$POSTGRES_DB"'],
                    env, stream)
            check_counts(compose, service, backup / f"{name}.counts", env)
        print("Checking restored files and real vector search, without generation or reindexing.", flush=True)
        result = run(compose + ["run", "--rm", "-T", "--no-deps", "--entrypoint", "python",
                                "vedomo-api", "/run/check-restored-vedomo.py"], env)
        print(result, flush=True)
        run(compose + ["up", "-d", "--no-build", "--pull", "never", "--wait", "--wait-timeout", "180"], env)
        print("Restored applications and HTTPS gateway are ready.", flush=True)
        result = run(compose + ["exec", "-T", "web", "node", "node_modules/tsx/dist/cli.mjs",
                                "scripts/max-full-restore-smoke.ts"], env)
        print(result, flush=True)
        check_counts(compose, "db", backup / "prodigy.counts", env)
        check_counts(compose, "vedomo-db", backup / "vedomo.counts", env)
        print("FULL_RESTORE_PASSED: new databases, retained files/index and HTTPS application flow.", flush=True)
    finally:
        if started:
            run(compose + ["stop", "--timeout", "30"], env)
            print(f"Only {args.project} stopped. Volumes and backup kept; nothing deleted.", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        if isinstance(error, RuntimeError):
            print(str(error), flush=True)
        raise SystemExit(f"FULL_RESTORE_FAILED ({type(error).__name__}); private output withheld.") from None
