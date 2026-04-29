"""One-shot translation driver: source.pdf (Arabic) -> English via DeepSeek.

Run: .venv/bin/python translate_run.py [output_filename]
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from app.api import run_job
from app.llm.client import set_stub_translator
from app.schemas import OutputMode, TranslationJob

load_dotenv()


async def main() -> None:
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        print("DEEPSEEK_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    src = Path(__file__).parent / "source.pdf"
    if not src.exists():
        print(f"missing {src}", file=sys.stderr)
        sys.exit(1)

    set_stub_translator(None)

    job = TranslationJob(
        src_path=src,
        target_lang="ar",
        source_lang="en",
        provider="deepseek",
        model="deepseek-chat",
        api_key=api_key,
        temperature=0.2,
        output_mode=OutputMode.TRANSLATED,
    )

    def _prog(f: float, msg: str) -> None:
        sys.stdout.write(f"\r[{f * 100:5.1f}%] {msg:<60}")
        sys.stdout.flush()

    out = await run_job(job, progress=_prog)
    print(f"\nWROTE: {out}")

    target_name = sys.argv[1] if len(sys.argv) > 1 else "translated.pdf"
    target = Path(__file__).parent / target_name
    target.write_bytes(Path(out).read_bytes())
    print(f"COPIED-TO: {target}")


if __name__ == "__main__":
    asyncio.run(main())
