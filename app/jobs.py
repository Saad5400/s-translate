"""Job registry — persists translation jobs so results are retrievable by ID.

Each job is stored at {temp_dir}/jobs/{job_id}/ with:
  - input.<ext>   uploaded source file
  - output.<ext>  translated/combined output (when complete)
  - meta.json     { id, status, progress, message, target_lang, provider, model,
                    output_mode, input_name, output_name, created_at, updated_at, error? }

Jobs can be queried by ID, listed, or deleted. A lightweight TTL sweeper
removes jobs older than N days.
"""
from __future__ import annotations

import json
import logging
import shutil
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from .config import settings

log = logging.getLogger(__name__)

JobStatus = Literal["queued", "running", "done", "failed"]


def _jobs_root() -> Path:
    root = settings.temp_dir / "jobs"
    root.mkdir(parents=True, exist_ok=True)
    return root


@dataclass
class JobMeta:
    id: str
    status: JobStatus = "queued"
    progress: float = 0.0
    message: str = ""
    target_lang: str = ""
    provider: str = ""
    model: str = ""
    output_mode: str = ""
    input_name: str = ""
    output_name: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def new_job_id() -> str:
    return uuid.uuid4().hex[:16]


def job_dir(job_id: str, create: bool = False) -> Path:
    p = _jobs_root() / job_id
    if create:
        p.mkdir(parents=True, exist_ok=True)
    return p


def save_meta(meta: JobMeta) -> None:
    meta.updated_at = time.time()
    d = job_dir(meta.id, create=True)
    (d / "meta.json").write_text(
        json.dumps(meta.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def load_meta(job_id: str) -> JobMeta | None:
    p = job_dir(job_id) / "meta.json"
    if not p.exists():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    return JobMeta(**data)


def update_status(
    job_id: str,
    status: JobStatus | None = None,
    progress: float | None = None,
    message: str | None = None,
    error: str | None = None,
    output_name: str | None = None,
) -> JobMeta | None:
    meta = load_meta(job_id)
    if meta is None:
        return None
    if status is not None:
        meta.status = status
    if progress is not None:
        meta.progress = progress
    if message is not None:
        meta.message = message
    if error is not None:
        meta.error = error
    if output_name is not None:
        meta.output_name = output_name
    save_meta(meta)
    return meta


def list_jobs(limit: int = 50) -> list[JobMeta]:
    out: list[JobMeta] = []
    for d in _jobs_root().iterdir():
        if not d.is_dir():
            continue
        meta = load_meta(d.name)
        if meta:
            out.append(meta)
    out.sort(key=lambda m: m.created_at, reverse=True)
    return out[:limit]


def delete_job(job_id: str) -> bool:
    d = job_dir(job_id)
    if not d.exists():
        return False
    shutil.rmtree(d, ignore_errors=True)
    return True


def sweep_old_jobs(max_age_seconds: int = 7 * 24 * 3600) -> int:
    """Delete jobs older than max_age_seconds. Returns count removed."""
    removed = 0
    cutoff = time.time() - max_age_seconds
    for d in _jobs_root().iterdir():
        if not d.is_dir():
            continue
        meta = load_meta(d.name)
        if meta and meta.created_at < cutoff:
            shutil.rmtree(d, ignore_errors=True)
            removed += 1
    return removed


def input_path(job_id: str, filename: str) -> Path:
    safe = Path(filename).name
    d = job_dir(job_id, create=True) / "input"
    d.mkdir(parents=True, exist_ok=True)
    return d / safe


def output_path(job_id: str, filename: str) -> Path:
    safe = Path(filename).name
    d = job_dir(job_id, create=True) / "output"
    d.mkdir(parents=True, exist_ok=True)
    return d / safe
