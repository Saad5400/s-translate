# s-trans — Document Translator

Self-hostable web app that translates `.docx`, `.pdf`, `.pptx`, `.xlsx`, and `.txt` files while **preserving the original formatting** — same fonts, styles, tables, images, layout. Supports RTL languages (Arabic, Hebrew, Persian, Urdu) with **full page-layout mirroring** for target-RTL output. Works with any AI provider through [LiteLLM](https://docs.litellm.ai/) (OpenAI, Anthropic, Gemini, DeepSeek, Mistral, Groq, Azure, Ollama, …).

## Features

- **Style-preserving translation** of paragraphs, runs, headings, tables, cells, fonts, colors.
- **RTL page mirror**: when the target is Arabic/Hebrew/Persian/Urdu the whole layout flips — text blocks swap sides, image positions are mirrored, but the image content itself stays unflipped (readable).
- **Bring-your-own model**: paste your API key + model string; no credentials are logged or persisted to disk.
- **Four output modes**:
  - Translated only
  - Original only
  - Both — vertical (original pages, then translated pages)
  - Both — horizontal (input always on left, translated always on right, same page)
- **Persistent jobs**:
  - Each translation gets a stable `job_id` on disk (`/tmp/s-trans/jobs/{id}/`)
  - Result stays retrievable via `GET /api/jobs/{id}/download` for 7 days (configurable)
  - URL hash `#job=<id>` auto-restores the last result when you reopen the browser tab
  - Settings (target language, provider, model, API base, output mode) are saved to `localStorage`
- **Gradio UI** at `/`, **REST API** at `POST /api/jobs` (async) or `POST /api/translate` (sync).
- **Docker**-ready with bundled LibreOffice + Noto fonts.

## Quick start (local)

```bash
# Python backend
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"

# Web UI (Vite + React + Tailwind + Radix + cmdk)
npm --prefix app/web install
npm --prefix app/web run build

.venv/bin/python -m app.main
# open http://localhost:7860
```

During UI development, `npm --prefix app/web run dev` runs Vite on port 5173
with `/api` proxied to the running FastAPI server.

## Docker

```bash
docker-compose up --build
# open http://localhost:7860
```

## Fully local (no API keys, no cloud)

The bundled `docker-compose.yml` ships an optional [Ollama](https://ollama.com) service behind the `ollama` profile, so `docker compose up` runs only `s-trans` by default. Opt in when you want it:

```bash
# Start s-trans + ollama together
docker compose --profile ollama up -d

# Pull a model once
docker exec -it $(docker compose ps -q ollama) ollama pull qwen2.5:7b
```

Then in the UI at `http://localhost:7860`:

- **Provider:** `ollama`
- **Model:** `ollama/qwen2.5:7b`
- **API base:** `http://ollama:11434` (the s-trans container talks to the `ollama` service by name)
- **API key:** any non-empty string (Ollama ignores it; LiteLLM requires the field)

Already running Ollama on the host? Skip the profile and point s-trans at it instead:

```bash
OLLAMA_API_BASE=http://host.docker.internal:11434 docker compose up -d
```

Model recommendations by hardware:

| RAM  | Model                        | Pull command                     |
|------|------------------------------|----------------------------------|
| 8 GB | `qwen2.5:7b` (Q4_K_M, ~4.7 GB) | `ollama pull qwen2.5:7b`       |
| 16 GB| `qwen2.5:14b` (Q4_K_M, ~9 GB)  | `ollama pull qwen2.5:14b`      |
| 16 GB| `gemma3:12b` (~7 GB)           | `ollama pull gemma3:12b`       |

CPU-only inference is slow (2–8 tok/s). `CONCURRENT_CHUNKS=1` is set by default in compose for this reason. For NVIDIA GPUs, uncomment the `deploy:` block in `docker-compose.yml`.

## API usage

### Async (recommended)

```bash
# 1. Create job (returns immediately with an ID)
curl -X POST http://localhost:7860/api/jobs \
  -F "file=@report.docx" \
  -F "target_lang=ar" \
  -F "provider=deepseek" \
  -F "model=deepseek-chat" \
  -F "api_key=sk-..." \
  -F "output_mode=both_horizontal"
# -> {"id":"eb328b4b...","status_url":"/api/jobs/eb328b4b.../download",...}

# 2. Poll status
curl http://localhost:7860/api/jobs/eb328b4b...

# 3. Download when status == "done"
curl http://localhost:7860/api/jobs/eb328b4b.../download -o out.pdf
```

### Sync (blocks until complete)

```bash
curl -X POST http://localhost:7860/api/translate \
  -F "file=@report.docx" \
  -F "target_lang=ar" \
  -F "provider=deepseek" \
  -F "model=deepseek-chat" \
  -F "api_key=sk-..." \
  -F "output_mode=translated" \
  -o report_ar.docx
```

Output modes: `original`, `translated`, `both_vertical`, `both_horizontal`.

## Tests

```bash
.venv/bin/pytest             # offline, stub LLM (15 tests)
DEEPSEEK_API_KEY=... .venv/bin/python -m tests.live_test   # live LLM
```

## Providers (LiteLLM model strings)

| Provider | Model string | Notes |
|-|-|-|
| OpenAI | `openai/gpt-4o-mini` | |
| Anthropic | `anthropic/claude-3-5-sonnet-latest` | |
| DeepSeek | `deepseek/deepseek-chat` | cheap, good at Arabic |
| Google Gemini | `gemini/gemini-1.5-flash` | |
| Groq | `groq/llama-3.1-70b-versatile` | fast |
| Mistral | `mistral/mistral-large-latest` | |
| Ollama | `ollama/llama3.1` + `api_base=http://localhost:11434` | local |

See [LiteLLM docs](https://docs.litellm.ai/docs/providers) for the full list.

## Architecture

```
upload → detect source lang (fasttext-langdetect)
      → Translator.extract(path) -> Segment[…]   (bullets / colors / fonts captured)
      → LLM translate (JSON-keyed batches, retry, parallel, placeholder masking)
      → Translator.reinsert(path, segments, out)
      → if RTL target:
          DOCX/XLSX/PPTX: apply w:bidi / sheet_view.rightToLeft / a:p rtl=1
          PDF: mirror the whole page layout — flipped raster bg (compressed JPEG),
               image bboxes mirrored but content un-flipped, translated text at
               mirrored positions; bg color sampled from the original page
      → if combine mode: append pages / side-by-side composite
          DOCX/PPTX horizontal: convert to PDF via LibreOffice, then composite
      → download via stable job id
```

### Key implementation notes

- **DOCX/PPTX inline runs**: preserved via `⟦n⟧...⟦/n⟧` sentinels the LLM is instructed to keep at the same positions. Fallback if counts mismatch: dump into the first run.
- **Placeholders**: URLs, emails, phone numbers are masked with `⟨Pn⟩` tokens that the LLM is told to leave verbatim; restored afterwards.
- **PDF bullet/numbering**: leading `•`, `■`, `-`, `1.` etc. detected at extraction and re-emitted on the appropriate side of the bbox after translation.
- **PDF file size**: original images are re-inserted via `extract_image` (compressed bytes, not raw pixmaps) — the pre-fix code ballooned 5 MB inputs to 50 MB; now they're ~2-3× input.
- **PDF backgrounds**: `fill=None` on redaction preserves any underlying image or colored strip; bg color for translated text is sampled from the original page at the original bbox so badges and colored strips aren't whited out.
- **Security**: API keys are accepted per-request only, never logged, never persisted. Job dirs contain input and output files but no credentials.
- **TTL cleanup**: hourly sweep removes jobs older than `JOB_TTL_SECONDS` (default 7 days).

## Directory layout

```
/tmp/s-trans/
├── jobs/
│   └── <job_id>/
│       ├── meta.json           status, progress, timestamps
│       ├── input/<filename>    the uploaded source
│       └── output/<filename>   translated result
└── uploads/                    temporary upload dir for /api/translate (sync)
```

## Environment variables

| Var | Default | Meaning |
|-|-|-|
| `HOST` | `0.0.0.0` | bind host |
| `PORT` | `7860` | bind port |
| `MAX_UPLOAD_MB` | `50` | upload size limit |
| `TEMP_DIR` | `/tmp/s-trans` | working dir (jobs, uploads) |
| `LOG_LEVEL` | `INFO` | |
| `DEFAULT_CHUNK_TOKENS` | `2500` | max tokens per LLM call |
| `CONCURRENT_CHUNKS` | `4` | parallel LLM requests per job |
| `LIBREOFFICE_BIN` | `soffice` | path to LibreOffice headless |
| `JOB_TTL_SECONDS` | `604800` (7 days) | job dir retention |

## What's preserved / what's lost

| | preserved | limitation |
|-|-|-|
| DOCX | styles, runs, tables, headers, footers, numbering, RTL attrs | text inside images not translated (OCR not yet wired) |
| PPTX | shapes, text frames, notes, fills, RTL per-paragraph | SmartArt text inside inline images stays |
| XLSX | cell fonts, fills, borders, merged cells, sheet RTL | formulas not translated (by design) |
| PDF (LTR target) | page structure, images, most backgrounds, fonts (fallback to Noto for translated runs) | line-art graphics may shift if bbox redraws reposition them |
| PDF (RTL target) | same + mirrored layout, unflipped image content | rasterized bg adds ~50-100 KB per page at 96 DPI (toggled off for pure-text pages) |
| TXT | — | plain UTF-8 with RLM+BOM prefix for RTL |
