"""Offline archive safety regressions, without Docker or production backups."""
import gzip
import hashlib
import io
import tarfile

import pytest
from scripts.backup_artifacts import verify, rotate

STAMP = "20260914-120000"


def snapshot(directory, entries=None, stamp=STAMP):
    db = directory / f"db-{stamp}.sql.gz"
    db.write_bytes(gzip.compress(b"SELECT 1;"))
    archive = directory / f"files-{stamp}.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for name in ["app/data", "app/docs"]:
            entry = tarfile.TarInfo(name); entry.type = tarfile.DIRTYPE
            tar.addfile(entry)
        for entry in entries or []:
            tar.addfile(entry, io.BytesIO(b"x" * entry.size) if entry.isfile() else None)
    manifest = directory / f"manifest-{stamp}.sha256"
    manifest.write_text("".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n" for p in [db, archive]))
    return db, archive, manifest


def test_valid_snapshot(tmp_path):
    snapshot(tmp_path)
    verify(tmp_path, STAMP)


def test_manifest_required(tmp_path):
    _, _, manifest = snapshot(tmp_path)
    manifest.unlink()
    with pytest.raises(FileNotFoundError): verify(tmp_path, STAMP)


def test_tampered_archive(tmp_path):
    db, _, _ = snapshot(tmp_path)
    db.write_bytes(b"damaged")
    with pytest.raises(ValueError, match="checksum"): verify(tmp_path, STAMP)


@pytest.mark.parametrize("name", ["/etc/passwd", "app/docs/../../etc/passwd", "app/other/file", "app/docs/..\\outside"])
def test_unsafe_paths(tmp_path, name):
    snapshot(tmp_path, [tarfile.TarInfo(name)])
    with pytest.raises(ValueError): verify(tmp_path, STAMP)


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE])
def test_links_and_special_files_rejected(tmp_path, kind):
    entry = tarfile.TarInfo("app/docs/link"); entry.type = kind; entry.linkname = "/etc/passwd"
    snapshot(tmp_path, [entry])
    with pytest.raises(ValueError, match="links/devices"): verify(tmp_path, STAMP)


def test_manifest_must_cover_both_files(tmp_path):
    _, _, manifest = snapshot(tmp_path)
    manifest.write_text(manifest.read_text().splitlines()[0] + "\n")
    with pytest.raises(ValueError, match="both"): verify(tmp_path, STAMP)


def test_timestamp_validation(tmp_path):
    with pytest.raises(ValueError, match="timestamp"): verify(tmp_path, "../../other")


def test_rotation_preserves_newest_set_and_unrelated_files(tmp_path):
    old = snapshot(tmp_path, stamp="20260913-120000")
    new = snapshot(tmp_path)
    keep = tmp_path / "operator notes.txt"; keep.write_text("keep")
    rotate(tmp_path, 1)
    assert all(not p.exists() for p in old)
    assert all(p.exists() for p in new) and keep.exists()


def test_rotation_without_optional_secret_or_any_snapshots(tmp_path):
    rotate(tmp_path, 14)
    snapshot(tmp_path)
    rotate(tmp_path, 14)
    verify(tmp_path, STAMP)
