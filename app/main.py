from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import jobs as jobs_mod
from .api import run_job
from .config import settings
from .schemas import OutputMode, TranslationJob

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("s-trans")


def create_app() -> FastAPI:
    fastapi_app = FastAPI(title="s-trans", version="0.1.0")

    # Sweep jobs older than JOB_TTL_DAYS on startup and then hourly.
    @fastapi_app.on_event("startup")
    async def _on_startup() -> None:
        import asyncio as _a
        from .config import settings as _s

        async def _sweep_loop() -> None:
            while True:
                try:
                    removed = jobs_mod.sweep_old_jobs(
                        max_age_seconds=getattr(_s, "job_ttl_seconds", 7 * 24 * 3600)
                    )
                    if removed:
                        log.info("sweeper removed %d old jobs", removed)
                except Exception:
                    log.exception("sweeper failed")
                await _a.sleep(3600)

        _a.create_task(_sweep_loop())

    @fastapi_app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    # --- Async job-based translate ------------------------------------------
    @fastapi_app.post("/api/jobs")
    async def create_job(
        background_tasks: BackgroundTasks,
        file: UploadFile = File(...),
        target_lang: str = Form(...),
        provider: str = Form(...),
        model: str = Form(...),
        api_key: str = Form(...),
        api_base: str | None = Form(None),
        temperature: float = Form(0.2),
        output_mode: str = Form(OutputMode.TRANSLATED.value),
        max_chunk_tokens: int = Form(2500),
    ) -> JSONResponse:
        try:
            mode = OutputMode(output_mode)
        except ValueError as exc:
            raise HTTPException(422, f"invalid output_mode: {exc}") from exc

        job_id = jobs_mod.new_job_id()
        safe_name = Path(file.filename or "upload").name
        in_path = jobs_mod.input_path(job_id, safe_name)
        with in_path.open("wb") as f:
            while chunk := await file.read(1024 * 1024):
                f.write(chunk)

        meta = jobs_mod.JobMeta(
            id=job_id,
            status="queued",
            target_lang=target_lang,
            provider=provider,
            model=model,
            output_mode=output_mode,
            input_name=safe_name,
        )
        jobs_mod.save_meta(meta)

        async def _runner() -> None:
            def _progress(f: float, msg: str) -> None:
                jobs_mod.update_status(job_id, progress=f, message=msg, status="running")

            job = TranslationJob(
                src_path=in_path,
                target_lang=target_lang,
                provider=provider,
                model=model,
                api_key=api_key,
                api_base=api_base,
                temperature=temperature,
                output_mode=mode,
                max_chunk_tokens=max_chunk_tokens,
            )
            try:
                jobs_mod.update_status(job_id, status="running", progress=0.0, message="Starting")
                out = await run_job(job, progress=_progress, job_id=job_id)
                jobs_mod.update_status(
                    job_id,
                    status="done",
                    progress=1.0,
                    message="Done",
                    output_name=out.name,
                )
            except Exception as exc:
                log.exception("job %s failed", job_id)
                jobs_mod.update_status(
                    job_id, status="failed", error=str(exc), message="Failed"
                )

        # Run asynchronously without blocking the request.
        asyncio.create_task(_runner())

        return JSONResponse(
            {
                "id": job_id,
                "status_url": f"/api/jobs/{job_id}",
                "download_url": f"/api/jobs/{job_id}/download",
            },
            status_code=202,
        )

    @fastapi_app.get("/api/jobs")
    async def list_jobs_api(limit: int = 50) -> dict:
        return {"jobs": [m.to_dict() for m in jobs_mod.list_jobs(limit=limit)]}

    @fastapi_app.get("/api/jobs/{job_id}")
    async def get_job(job_id: str) -> dict:
        meta = jobs_mod.load_meta(job_id)
        if not meta:
            raise HTTPException(404, "job not found")
        body = meta.to_dict()
        if meta.status == "done":
            body["download_url"] = f"/api/jobs/{job_id}/download"
        return body

    @fastapi_app.get("/api/jobs/{job_id}/download")
    async def download_job(job_id: str) -> FileResponse:
        meta = jobs_mod.load_meta(job_id)
        if not meta:
            raise HTTPException(404, "job not found")
        if meta.status != "done":
            raise HTTPException(425, f"job status: {meta.status}")
        out_dir = jobs_mod.job_dir(job_id) / "output"
        candidate = out_dir / meta.output_name if meta.output_name else None
        if candidate and candidate.exists():
            out_file = candidate
        else:
            files = list(out_dir.glob("*")) if out_dir.exists() else []
            if not files:
                # legacy layout fallback
                files = list(jobs_mod.job_dir(job_id).glob("output_*"))
            if not files:
                raise HTTPException(404, "output file missing")
            out_file = files[0]
        return FileResponse(
            str(out_file), filename=out_file.name,
            media_type="application/octet-stream",
        )

    @fastapi_app.delete("/api/jobs/{job_id}")
    async def delete_job(job_id: str) -> dict:
        ok = jobs_mod.delete_job(job_id)
        if not ok:
            raise HTTPException(404, "job not found")
        return {"deleted": job_id}

    # --- Legacy synchronous translate (kept for backwards compat) -----------
    @fastapi_app.post("/api/translate")
    async def translate(
        file: UploadFile = File(...),
        target_lang: str = Form(...),
        provider: str = Form(...),
        model: str = Form(...),
        api_key: str = Form(...),
        api_base: str | None = Form(None),
        temperature: float = Form(0.2),
        output_mode: str = Form(OutputMode.TRANSLATED.value),
        max_chunk_tokens: int = Form(2500),
    ) -> FileResponse:
        upload_dir = settings.temp_dir / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        safe_name = Path(file.filename or "upload").name
        dest = upload_dir / safe_name
        with dest.open("wb") as f:
            while chunk := await file.read(1024 * 1024):
                f.write(chunk)
        try:
            mode = OutputMode(output_mode)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"invalid output_mode: {exc}") from exc
        job = TranslationJob(
            src_path=dest, target_lang=target_lang, provider=provider, model=model,
            api_key=api_key, api_base=api_base, temperature=temperature,
            output_mode=mode, max_chunk_tokens=max_chunk_tokens,
        )
        try:
            out_path = await run_job(job)
        except Exception as exc:
            log.exception("translate failed")
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return FileResponse(
            str(out_path), filename=out_path.name, media_type="application/octet-stream",
        )

    # Serve the static web UI (React SPA) at root. The Vite build output
    # lives in app/web/dist; we only mount if it exists so `uvicorn` still
    # boots cleanly in a fresh checkout before `npm run build`.
    web_dist = Path(__file__).parent / "web" / "dist"
    if web_dist.exists():
        fastapi_app.mount(
            "/", StaticFiles(directory=str(web_dist), html=True), name="web"
        )
    else:
        log.warning(
            "web UI not built; run 'npm --prefix app/web install && npm --prefix app/web run build'"
        )
    return fastapi_app


app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
