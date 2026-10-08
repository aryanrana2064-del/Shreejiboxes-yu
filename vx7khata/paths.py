"""Where the application keeps its data."""
from __future__ import annotations

import os
from pathlib import Path

DB_FILENAME = "khata.db"


def data_dir() -> Path:
    """Per-user data folder. ``VX7_DATA_DIR`` overrides it (portable use / tests)."""
    override = os.environ.get("VX7_DATA_DIR")
    if override:
        base = Path(override)
    elif os.name == "nt":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "VX7 KHATA PRO"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "vx7-khata-pro"
    base.mkdir(parents=True, exist_ok=True)
    return base


def db_path() -> Path:
    return data_dir() / DB_FILENAME


def safety_backup_dir() -> Path:
    p = data_dir() / "safety_backups"
    p.mkdir(parents=True, exist_ok=True)
    return p
