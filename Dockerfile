FROM node:22-slim AS web-build
WORKDIR /web
COPY app/web/package.json app/web/package-lock.json* ./
RUN npm ci || npm install
COPY app/web ./
RUN npm run build


FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# System deps: LibreOffice for DOCX/PPTX -> PDF, Noto fonts for non-Latin scripts,
# curl for optional health checks.
RUN apt-get update && apt-get install -y --no-install-recommends \
      libreoffice-core \
      libreoffice-writer \
      libreoffice-impress \
      libreoffice-calc \
      fonts-noto \
      fonts-noto-cjk \
      fonts-noto-extra \
      fonts-noto-color-emoji \
      ghostscript \
      curl \
    && rm -rf /var/lib/apt/lists/* \
    && fc-cache -f -v > /dev/null

WORKDIR /app

# Install Python deps first for better layer caching.
COPY pyproject.toml ./
RUN pip install --upgrade pip \
    && pip install \
        "fastapi>=0.115" "uvicorn[standard]>=0.32" \
        "pydantic>=2.9" "pydantic-settings>=2.5" "python-multipart>=0.0.12" \
        "litellm>=1.50" "tenacity>=9.0" \
        "python-docx>=1.1" "python-pptx>=1.0" "openpyxl>=3.1" \
        "pymupdf>=1.24" "pypdf>=5.0" "docxcompose>=1.4" \
        "fast-langdetect>=0.2" "arabic-reshaper>=3.0" "python-bidi>=0.6" \
        "lxml>=5.3" "tiktoken>=0.8"

# App sources
COPY app ./app
COPY README.md ./
# Drop the dev UI tree and splice in the built assets only.
RUN rm -rf /app/app/web && mkdir -p /app/app/web/dist
COPY --from=web-build /web/dist /app/app/web/dist

ENV HOST=0.0.0.0 \
    PORT=7860 \
    TEMP_DIR=/tmp/s-trans \
    LIBREOFFICE_BIN=/usr/bin/soffice

EXPOSE 7860

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fs http://localhost:7860/health || exit 1

CMD ["python", "-m", "app.main"]
