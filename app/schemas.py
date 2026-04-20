from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class OutputMode(str, Enum):
    ORIGINAL = "original"
    TRANSLATED = "translated"
    BOTH_VERTICAL = "both_vertical"
    BOTH_HORIZONTAL = "both_horizontal"


class DocFormat(str, Enum):
    DOCX = "docx"
    PPTX = "pptx"
    XLSX = "xlsx"
    PDF = "pdf"
    TXT = "txt"

    @classmethod
    def from_path(cls, path: str | Path) -> "DocFormat":
        ext = Path(path).suffix.lower().lstrip(".")
        try:
            return cls(ext)
        except ValueError as exc:
            raise ValueError(f"Unsupported format: {ext}") from exc


@dataclass
class Segment:
    """A unit of translatable text with metadata for reinsertion."""
    id: str
    text: str
    meta: dict[str, Any] = field(default_factory=dict)
    translated: str | None = None


@dataclass
class TranslationJob:
    src_path: Path
    target_lang: str
    provider: str
    model: str
    api_key: str
    api_base: str | None = None
    temperature: float = 0.2
    output_mode: OutputMode = OutputMode.TRANSLATED
    max_chunk_tokens: int = 2500
    source_lang: str | None = None  # auto-detect if None

    @property
    def model_string(self) -> str:
        """LiteLLM combined model string: provider/model."""
        if "/" in self.model:
            return self.model
        return f"{self.provider}/{self.model}"
