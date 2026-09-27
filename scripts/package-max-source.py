"""Package current source files, never local databases, caches or credentials."""

import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
REPOSITORIES = {"prodigy": ROOT, "vedomo": ROOT.parent / "vedomo"}
OUTPUT = ROOT / "output/contest/max-source-20260927.zip"
EXCLUDED = {"node_modules", ".next", ".venv", "venv", "__pycache__", ".git",
            "output", "tmp", "backups", "data", "exports", ".pytest_cache"}
PRIVATE_SUFFIXES = {".pem", ".key", ".crt", ".pfx", ".db", ".sqlite", ".dump", ".tar", ".zip"}


def git(repository, *args):
    return subprocess.check_output(["git", "-C", str(repository), *args])


def included(name):
    parts = PurePosixPath(name).parts
    if not parts or ".." in parts or any(part in EXCLUDED for part in parts):
        return False
    filename = parts[-1]
    if ".env" in filename and not filename.endswith(".example"):
        return False
    if Path(filename).suffix.lower() in PRIVATE_SUFFIXES:
        return False
    return not (len(parts) > 1 and parts[0] == "public" and parts[1] in {"uploads", "branding"})


def private_values():
    values = set()
    for repository in REPOSITORIES.values():
        candidates = list(repository.glob(".env*"))
        candidates += list((repository / "deploy/max").glob(".env*"))
        for candidate in candidates:
            if not candidate.is_file() or candidate.name.endswith(".example"):
                continue
            for line in candidate.read_text(encoding="utf-8-sig").splitlines():
                name, separator, value = line.partition("=")
                if separator and any(word in name.upper() for word in ("TOKEN", "KEY", "SECRET", "PASSWORD", "PASS")):
                    value = value.strip().strip("\"'")
                    if len(value) >= 16:
                        values.add(value.encode("utf-8"))
    return values


def main():
    if OUTPUT.exists():
        raise SystemExit("Source archive already exists. Its fixed version was not replaced.")
    secrets = private_values()
    records = []
    files = []
    heads = {}
    total = 0
    for label, repository in REPOSITORIES.items():
        heads[label] = git(repository, "rev-parse", "HEAD").decode().strip()
        names = git(repository, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
        for name in sorted(set(names.decode("utf-8").split("\0"))):
            if not included(name):
                continue
            source = repository / name
            if not source.exists():
                continue
            if not source.is_file() or source.is_symlink() or not source.resolve().is_relative_to(repository):
                raise SystemExit("A source path leaves its repository or is not a regular file.")
            data = source.read_bytes()
            total += len(data)
            if len(data) > 20 * 1024 * 1024 or total > 100 * 1024 * 1024:
                raise SystemExit("Archive exceeds the small-source size limit; nothing was packaged.")
            if any(secret in data for secret in secrets):
                raise SystemExit("A local private credential appears in a source file; archive cancelled.")
            archive_name = f"{label}/{name}"
            files.append((archive_name, data))
            records.append({"path": archive_name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})
    manifest = json.dumps({
        "version": "max-source-20260927",
        "basis": "current working files, including uncommitted changes",
        "base_commits": heads,
        "files": records,
    }, ensure_ascii=False, indent=2).encode("utf-8")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(OUTPUT, "x", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, data in [*files, ("SOURCE-MANIFEST.json", manifest)]:
            entry = zipfile.ZipInfo(name, date_time=(2026, 9, 27, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, data)
    with zipfile.ZipFile(OUTPUT) as archive:
        assert archive.testzip() is None
        for record in records:
            assert hashlib.sha256(archive.read(record["path"])).hexdigest() == record["sha256"]
    checksum = hashlib.sha256(OUTPUT.read_bytes()).hexdigest()
    with OUTPUT.with_suffix(".sha256").open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(f"{checksum}  {OUTPUT.name}\n")
    print(f"Source archive verified: {len(files)} files, {OUTPUT.stat().st_size} bytes, SHA256 {checksum}.")


if __name__ == "__main__":
    main()
