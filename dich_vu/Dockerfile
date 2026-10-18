# One image for every block of the slide; each container runs `dich-vu serve <block>`.
# Build context is the repository root: the services run the data_pipeline core.
FROM python:3.13-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.12.13 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright \
    PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1

# LibreOffice converts .doc/.ppt/.odt/.rtf before parsing (Đọc nội dung).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libreoffice-writer-nogui libreoffice-calc-nogui libreoffice-impress-nogui \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY data_pipeline/pyproject.toml data_pipeline/uv.lock data_pipeline/
COPY dich_vu/pyproject.toml dich_vu/uv.lock dich_vu/
RUN mkdir -p data_pipeline/src/data_pipeline dich_vu/src/dich_vu \
    && touch data_pipeline/src/data_pipeline/__init__.py dich_vu/src/dich_vu/__init__.py \
    && uv sync --frozen --no-dev --no-install-project --project dich_vu
# Chromium for the Playwright fallback of Tải về (before src, so code changes keep this layer).
RUN playwright install --with-deps chromium && rm -rf /var/lib/apt/lists/*
COPY data_pipeline/src data_pipeline/src
COPY dich_vu/src dich_vu/src
RUN uv sync --frozen --no-dev --project dich_vu

COPY data_pipeline/config data_pipeline/config
RUN useradd --create-home --uid 1000 app \
    && mkdir -p /app/data_pipeline/data/enrichment/cache /app/data_pipeline/data/state \
    && chown -R app /app/data_pipeline/data
USER app

# Paths in config/*.yaml are relative to the data_pipeline project, as on the host.
WORKDIR /app/data_pipeline
ENTRYPOINT ["dich-vu"]
