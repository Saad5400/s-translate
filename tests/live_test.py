"""LIVE end-to-end test using a real LLM (DeepSeek). Run with:
    DEEPSEEK_API_KEY=sk-xxx .venv/bin/python -m tests.live_test
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from app.api import run_job
from app.llm.client import set_stub_translator
from app.schemas import OutputMode, TranslationJob
from tests.make_fixtures import make_all

FIXTURES = Path(__file__).parent / "fixtures"


async def run_one(src: Path, target: str, mode: OutputMode, api_key: str) -> Path:
    job = TranslationJob(
        src_path=src,
        target_lang=target,
        provider="deepseek",
        model="deepseek-chat",
        api_key=api_key,
        temperature=0.2,
        output_mode=mode,
    )

    def _prog(f: float, msg: str) -> None:
        sys.stdout.write(f"\r[{src.name} {target} {mode.value}] {msg} ({f*100:.0f}%)        ")
        sys.stdout.flush()

    out = await run_job(job, progress=_prog)
    print(f"\n  -> {out}")
    return out


async def main() -> None:
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        print("Set DEEPSEEK_API_KEY env var")
        sys.exit(1)

    set_stub_translator(None)  # ensure real path
    make_all()

    # Run a focused set: every format translated to Arabic (RTL) and to Spanish (LTR).
    fmts = ["txt", "xlsx", "docx", "pptx", "pdf"]
    for fmt in fmts:
        src = FIXTURES / f"sample.{fmt}"
        for target in ("ar", "es"):
            await run_one(src, target, OutputMode.TRANSLATED, api_key)

    # Combined modes on one fixture each.
    await run_one(FIXTURES / "sample.docx", "ar", OutputMode.BOTH_HORIZONTAL, api_key)
    await run_one(FIXTURES / "sample.pdf", "ar", OutputMode.BOTH_VERTICAL, api_key)


if __name__ == "__main__":
    asyncio.run(main())
