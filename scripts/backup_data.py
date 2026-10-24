"""Create a private backup archive of the configured CertifyFlow data directory."""
import os
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

data_dir = Path(os.getenv("CERTIFYFLOW_DATA_DIR", "./data")).resolve()
backup_dir = Path(os.getenv("CERTIFYFLOW_BACKUP_DIR", "./backups")).resolve()
database = data_dir / "certifyflow.sqlite3"
if not database.is_file():
    raise SystemExit(f"Database not found: {database}")
backup_dir.mkdir(parents=True, exist_ok=True)
stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
archive_path = backup_dir / f"certifyflow-{stamp}.zip"

with tempfile.TemporaryDirectory(prefix="certifyflow-backup-") as tmp:
    snapshot = Path(tmp) / "certifyflow.sqlite3"
    source = sqlite3.connect(database)
    target = sqlite3.connect(snapshot)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(snapshot, "certifyflow.sqlite3")
        for folder in ("uploads", "outputs"):
            root = data_dir / folder
            archive.writestr(f"{folder}/", "")
            if root.is_dir():
                for item in root.rglob("*"):
                    if item.is_file():
                        archive.write(item, item.relative_to(data_dir).as_posix())

print(f"Created {archive_path} ({archive_path.stat().st_size:,} bytes)")
print("This archive contains participant data and must be stored securely.")
