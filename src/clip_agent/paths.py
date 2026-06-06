from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = Path(os.getenv("CLIPPER_DATA_DIR", str(PROJECT_ROOT))).expanduser().resolve()
RUNS_ROOT = DATA_ROOT / "runs"
CACHE_ROOT = DATA_ROOT / ".cache"
SECRETS_ROOT = DATA_ROOT / ".secrets"


def ensure_data_directories() -> None:
    for path in (DATA_ROOT, RUNS_ROOT, CACHE_ROOT, SECRETS_ROOT):
        path.mkdir(parents=True, exist_ok=True)


def resolve_data_path(path: Path) -> Path:
    if path.is_absolute():
        return path
    return DATA_ROOT / path
