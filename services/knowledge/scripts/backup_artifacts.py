"""Verify backup integrity and safe extraction paths before destructive restore."""
import argparse
import gzip
import hashlib
import re
import tarfile
import time
from pathlib import Path, PurePosixPath

STAMP = re.compile(r"\d{8}-\d{6}")


def verify(directory: Path, stamp: str):
    if not STAMP.fullmatch(stamp):
        raise ValueError("invalid snapshot timestamp")
    names = {f"db-{stamp}.sql.gz", f"files-{stamp}.tar.gz"}
    entries = {}
    for line in (directory / f"manifest-{stamp}.sha256").read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"([a-fA-F0-9]{64}) [ *]([^/\\]+)", line)
        if not match or match[2] not in names | {f"env-{stamp}.age"} or match[2] in entries:
            raise ValueError("unexpected or duplicate manifest entry")
        entries[match[2]] = match[1].lower()
    if not names <= entries.keys():
        raise ValueError("manifest must cover both snapshot archives")
    for name, digest in entries.items():
        path = directory / name
        if path.is_symlink() or not path.is_file():
            raise ValueError("artifact must be a regular file")
        with path.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != digest:
            raise ValueError(f"checksum mismatch: {name}")
    # Read to EOF to detect truncated gzip before stopping the API.
    for name in names:
        with gzip.open(directory / name, "rb") as stream:
            while stream.read(1024 * 1024):
                pass
    roots = set()
    with tarfile.open(directory / f"files-{stamp}.tar.gz", "r:gz") as archive:
        for member in archive:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or "\\" in member.name:
                raise ValueError("unsafe archive path")
            if len(path.parts) < 2 or path.parts[:2] not in {("app", "data"), ("app", "docs")}:
                raise ValueError("archive entry outside data/docs")
            if not (member.isfile() or member.isdir()):
                raise ValueError("links/devices are not allowed in a snapshot")
            if len(path.parts) == 2 and not member.isdir():
                raise ValueError("archive roots must be directories")
            roots.add(path.parts[:2])
    if roots != {("app", "data"), ("app", "docs")}:
        raise ValueError("snapshot must contain both data and docs")


def rotate(directory: Path, retain: int):
    if retain < 1:
        raise ValueError("retention must be positive")
    # Complete sets, exact validated names; never glob-delete arbitrary files.
    stamps = sorted((p.name[9:-7] for p in directory.glob("manifest-*.sha256")
                     if STAMP.fullmatch(p.name[9:-7])), reverse=True)
    for stamp in stamps[retain:]:
        for name in (f"db-{stamp}.sql.gz", f"files-{stamp}.tar.gz",
                     f"env-{stamp}.age", f"manifest-{stamp}.sha256"):
            (directory / name).unlink(missing_ok=True)


def check_latest(directory: Path, max_age_hours: int, *, now: float | None = None) -> int:
    """Check a published snapshot, never a fresh partial archive or log.

    Caller holds the shared backup/restore lock throughout this full verification.
    Returns age in hours; this checks local integrity, not offsite or recovery.
    """
    if max_age_hours < 1:
        raise ValueError("max age must be positive")
    manifests = [p for p in directory.glob("manifest-*.sha256")
                 if STAMP.fullmatch(p.name[9:-7])]
    if not manifests:
        raise ValueError("no published snapshot")
    latest = max(manifests, key=lambda p: p.name)
    if latest.is_symlink() or not latest.is_file():
        raise ValueError("manifest must be a regular file")
    age = (time.time() if now is None else now) - latest.stat().st_mtime
    if age < 0 or age >= max_age_hours * 3600:
        raise ValueError("snapshot stale or clock skew")
    verify(directory, latest.name[9:-7])
    return int(age // 3600)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["verify", "rotate", "status"])
    parser.add_argument("directory", type=Path)
    parser.add_argument("value")
    args = parser.parse_args()
    if args.action == "verify":
        verify(args.directory, args.value)
    elif args.action == "rotate":
        rotate(args.directory, int(args.value))
    else:
        print(check_latest(args.directory, int(args.value)))
