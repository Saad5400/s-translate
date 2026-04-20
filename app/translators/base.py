from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from ..schemas import DocFormat, Segment


class Translator(ABC):
    """Abstract per-format translator.

    Workflow:
      segments = t.extract(src_path)          # parse -> list[Segment]
      # caller translates segments (sets .translated on each)
      t.reinsert(src_path, segments, out_path)  # write translated doc
    """

    fmt: DocFormat

    @abstractmethod
    def extract(self, src_path: Path) -> list[Segment]:
        ...

    @abstractmethod
    def reinsert(self, src_path: Path, segments: list[Segment], out_path: Path) -> Path:
        ...
