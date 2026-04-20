from __future__ import annotations

from typing import Callable

ProgressCB = Callable[[float, str], None]


def noop_progress(fraction: float, message: str) -> None:  # noqa: ARG001
    pass
