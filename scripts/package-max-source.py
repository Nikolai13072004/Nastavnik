"""Check or package the committed Nastavnik source without local secrets."""

import argparse
import hashlib
from io import BytesIO
import json
from pathlib import Path, PurePosixPath
import subprocess
import zipfile


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "output" / "contest"
EXCLUDED = {
    ".git", ".next", ".pytest_cache", ".venv", "__pycache__",
    "backups", "data", "exports", "node_modules", "output", "tmp", "venv",
}
PRIVATE_SUFFIXES = {".crt", ".db", ".dump", ".key", ".pem", ".pfx", ".sqlite", ".tar", ".zip"}
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_SOURCE_BYTES = 100 * 1024 * 1024
PRIVATE_KEY_MARKER = b"-----BEGIN " + b"PRIVATE KEY-----"


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(ROOT), *args])


def included(name: str) -> bool:
    parts = PurePosixPath(name).parts
    if not parts or ".." in parts or any(part.lower() in EXCLUDED for part in parts):
        return False

    filename = parts[-1]
    if ".env" in filename.lower() and not filename.lower().endswith(".example"):
        return False
    if Path(filename).suffix.lower() in PRIVATE_SUFFIXES:
        return False
    return not (len(parts) > 1 and parts[0] == "public" and parts[1] in {"uploads", "branding"})


def private_values() -> set[bytes]:
    values = set()
    for folder in (ROOT, ROOT / "deploy" / "max", ROOT / "services" / "knowledge"):
        for candidate in folder.glob(".env*"):
            if not candidate.is_file() or candidate.name.endswith(".example"):
                continue
            for line in candidate.read_text(encoding="utf-8-sig").splitlines():
                name, separator, value = line.partition("=")
                if separator and any(word in name.upper() for word in ("TOKEN", "KEY", "SECRET", "PASSWORD", "PASS")):
                    value = value.strip().strip("\"'")
                    if len(value) >= 16:
                        values.add(value.encode("utf-8"))
    return values


def source_files(check_worktree: bool):
    if check_worktree:
        names = git("ls-files", "-z", "--cached", "--others", "--exclude-standard")
        for name in sorted(set(names.decode("utf-8").split("\0"))):
            if not included(name):
                continue
            path = ROOT / name
            if not path.exists():
                continue
            if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(ROOT):
                raise SystemExit("Source contains a path outside the repository or a non-regular file.")
            yield name, path.read_bytes()
        return

    modes = {}
    for item in git("ls-tree", "-r", "-z", "HEAD").split(b"\0"):
        if item:
            metadata, name = item.split(b"\t", 1)
            modes[name.decode("utf-8")] = metadata.split(b" ", 1)[0]

    with zipfile.ZipFile(BytesIO(git("archive", "--format=zip", "HEAD"))) as source:
        for entry in sorted(source.infolist(), key=lambda item: item.filename):
            name = entry.filename
            if entry.is_dir() or not included(name):
                continue
            if modes.get(name) not in {b"100644", b"100755"}:
                raise SystemExit("The committed source contains a non-regular file.")
            yield name, source.read(entry)


def collect_files(check_worktree: bool):
    secrets = private_values()
    files = []
    records = []
    total = 0
    for name, data in source_files(check_worktree):
        total += len(data)
        if len(data) > MAX_FILE_BYTES or total > MAX_SOURCE_BYTES:
            raise SystemExit("Source exceeds the archive size limit.")
        if PRIVATE_KEY_MARKER in data or any(secret in data for secret in secrets):
            raise SystemExit(f"A private credential appears in {name}; archive cancelled.")
        archive_name = f"nastavnik/{name}"
        files.append((archive_name, data))
        records.append({"path": archive_name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})
    return files, records, total


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="scan the current source without writing an archive")
    args = parser.parse_args()

    commit = git("rev-parse", "HEAD").decode().strip()
    dirty = bool(git("status", "--porcelain", "--untracked-files=all"))
    if dirty and not args.check:
        raise SystemExit("Commit and verify the final source before creating its fixed archive.")

    files, records, total = collect_files(check_worktree=args.check)
    if args.check:
        state = "uncommitted changes remain" if dirty else "worktree is clean"
        print(f"Source scan passed: {len(files)} files, {total} bytes; {state}.")
        return

    output = OUTPUT_DIR / f"nastavnik-source-{commit[:12]}.zip"
    if output.exists() or output.with_suffix(".sha256").exists():
        raise SystemExit("A fixed archive or checksum already exists; nothing was replaced.")

    manifest = json.dumps({
        "product": "Наставник",
        "commit": commit,
        "basis": "committed Git tree at HEAD",
        "files": records,
    }, ensure_ascii=False, indent=2).encode("utf-8")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "x", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, data in [*files, ("SOURCE-MANIFEST.json", manifest)]:
            entry = zipfile.ZipInfo(name, date_time=(2026, 9, 28, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, data)

    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise SystemExit("Archive verification failed.")
        for record in records:
            if hashlib.sha256(archive.read(record["path"])).hexdigest() != record["sha256"]:
                raise SystemExit("Archive content verification failed.")

    checksum = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(".sha256").write_text(f"{checksum}  {output.name}\n", encoding="utf-8")
    print(f"Source archive verified: {len(files)} files, {output.stat().st_size} bytes, SHA256 {checksum}.")


if __name__ == "__main__":
    main()
