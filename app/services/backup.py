"""Local backup: zip marketing.db (+ chroma/ when present) into data/backups/.

Restore = stop app, unzip over data/, start app. No enterprise machinery.
Never includes .env or workspace archives.
"""
import zipfile
from datetime import datetime, timezone
from pathlib import Path


def create_backup(root: str | Path, db_path: str | Path) -> Path:
    root, db_path = Path(root), Path(db_path)
    dest_dir = root / "data" / "backups"
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    dest = dest_dir / f"backup-{stamp}.zip"
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(db_path, "marketing.db")
        chroma = root / "data" / "chroma"
        if chroma.is_dir():
            for f in sorted(chroma.rglob("*")):
                if f.is_file():
                    z.write(f, str(Path("chroma") / f.relative_to(chroma)))
    return dest


def list_backups(root: str | Path) -> list[Path]:
    d = Path(root) / "data" / "backups"
    return sorted(d.glob("backup-*.zip")) if d.is_dir() else []
