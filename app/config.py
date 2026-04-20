from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    host: str = "0.0.0.0"
    port: int = 7860
    max_upload_mb: int = 50
    temp_dir: Path = Path("/tmp/s-trans")
    log_level: str = "INFO"
    default_chunk_tokens: int = 2500
    concurrent_chunks: int = 4
    libreoffice_bin: str = "soffice"
    fonts_dir: Path = Path(__file__).parent / "fonts"
    job_ttl_seconds: int = 7 * 24 * 3600  # 7 days

    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")


settings = Settings()
settings.temp_dir.mkdir(parents=True, exist_ok=True)
