"""Restore a CertifyFlow backup. Stop the app before running this command."""
import os
import shutil
import sqlite3
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

if len(sys.argv) != 2:
    raise SystemExit("Usage: python scripts/restore_data.py <backup.zip>")

data_dir = Path(os.environ.get("CERTIFYFLOW_DATA_DIR", "./data")).resolve()
archive_path = Path(sys.argv[1]).resolve()
if not archive_path.is_file():
    raise SystemExit(f"Backup not found: {archive_path}")
data_dir.mkdir(parents=True, exist_ok=True)
stage = Path(tempfile.mkdtemp(prefix=".certifyflow-restore-", dir=data_dir))
new = stage / "new"
old = stage / "old"
new.mkdir()
old.mkdir()
targets = ("certifyflow.sqlite3", "certifyflow.sqlite3-wal", "certifyflow.sqlite3-shm", "uploads", "outputs")

try:
    with zipfile.ZipFile(archive_path) as archive:
        corrupt_member = archive.testzip()
        if corrupt_member:
            raise ValueError(f"Corrupt file in backup: {corrupt_member}")
        found_db = False
        for member in archive.infolist():
            name = member.filename
            if member.is_dir() and name in {"uploads/", "outputs/"}:
                (new / name.rstrip("/")).mkdir(exist_ok=True)
                continue
            relative = PurePosixPath(name)
            if relative.is_absolute() or ".." in relative.parts or "\\" in name:
                raise ValueError(f"Unsafe path in backup: {name}")
            if not relative.parts or relative.parts[0] not in {"certifyflow.sqlite3", "uploads", "outputs"}:
                raise ValueError(f"Unexpected file in backup: {name}")
            if relative.parts[0] == "certifyflow.sqlite3" and (len(relative.parts) != 1 or member.is_dir()):
                raise ValueError(f"Invalid database entry in backup: {name}")
            destination = new.joinpath(*relative.parts)
            if not destination.resolve().is_relative_to(new.resolve()):
                raise ValueError(f"Unsafe path in backup: {name}")
            if member.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, destination.open("wb") as output:
                shutil.copyfileobj(source, output)
            if name == "certifyflow.sqlite3":
                found_db = True
        if not found_db:
            raise ValueError("Backup does not contain certifyflow.sqlite3")
    for folder in ("uploads", "outputs"):
        (new / folder).mkdir(exist_ok=True)
    db = sqlite3.connect(new / "certifyflow.sqlite3")
    try:
        check = db.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        db.close()
    if check != "ok":
        raise ValueError(f"Backup database failed integrity check: {check}")

    moved_old, moved_new = [], []
    try:
        for name in targets:
            current = data_dir / name
            if current.exists():
                current.rename(old / name)
                moved_old.append(name)
        for name in ("certifyflow.sqlite3", "uploads", "outputs"):
            (new / name).rename(data_dir / name)
            moved_new.append(name)
    except Exception:
        for name in reversed(moved_new):
            current = data_dir / name
            if current.is_dir():
                shutil.rmtree(current)
            else:
                current.unlink(missing_ok=True)
        for name in moved_old:
            previous = old / name
            if previous.exists():
                previous.rename(data_dir / name)
        raise
    print(f"Restored CertifyFlow data from {archive_path}")
finally:
    shutil.rmtree(stage, ignore_errors=True)
