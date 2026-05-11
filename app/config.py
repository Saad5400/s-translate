from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    host: str = "0.0.0.0"
    port: int = 7860
    max_upload_mb: int = 50
    temp_dir: Path = Path("/tmp/s-trans")
    log_level: str = "INFO"
    log_file: Path | None = None  # default resolved to temp_dir/s-trans.log
    reuse_translations: bool = True
    default_chunk_tokens: int = 2500
    concurrent_chunks: int = 4
    # Cap on whole-document jobs that may run in parallel. Every upload goes
    # through a FIFO queue; this controls how many workers drain it. Set to 1
    # for a shared server (jobs serialize so the box isn't overloaded), or
    # higher when running locally on a powerful machine.
    max_concurrent_jobs: int = 4
    # Soft cap on per-document conversation history (tokens). When the next
    # chunk's prompt would exceed this, the oldest user/assistant chunk-turn
    # pair is evicted while the system prompt and the document intro/context
    # brief stay pinned. Use generous defaults for big-context models; smaller
    # local models should lower this in their .env.
    max_history_tokens: int = 96000
    libreoffice_bin: str = "soffice"
    fonts_dir: Path = Path(__file__).parent / "fonts"
    job_ttl_seconds: int = 7 * 24 * 3600  # 7 days
    pdf_ocr_languages: str = "eng+ara"
    # 0 means "let ocrmypdf pick (os.cpu_count())"; explicit integer caps it.
    pdf_ocr_jobs: int = 0

    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")


settings = Settings()
settings.temp_dir.mkdir(parents=True, exist_ok=True)
