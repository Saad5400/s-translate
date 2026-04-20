from __future__ import annotations

import shutil
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from ..config import settings


@contextmanager
def job_workspace() -> Iterator[Path]:
    """Create a temp workspace directory; clean up when done."""
    wd = settings.temp_dir / uuid.uuid4().hex
    wd.mkdir(parents=True, exist_ok=True)
    try:
        yield wd
    finally:
        shutil.rmtree(wd, ignore_errors=True)


def copy_to(src: Path, dst: Path) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return dst
