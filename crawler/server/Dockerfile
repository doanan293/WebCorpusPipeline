# One image for every block of the slide; each container runs `services serve <block>`.
# Build context is crawler/ (the folder holding core/ and server/): the services run the data_pipeline core.
FROM python:3.13-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.12.13 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright \
    PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1

# LibreOffice converts .doc/.ppt/.odt/.rtf before parsing (reader).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libreoffice-writer-nogui libreoffice-calc-nogui libreoffice-impress-nogui \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY core/pyproject.toml core/uv.lock data_pipeline/
COPY server/pyproject.toml server/uv.lock services/
RUN mkdir -p data_pipeline/src/data_pipeline services/src/services \
    && touch data_pipeline/src/data_pipeline/__init__.py services/src/services/__init__.py \
    && uv sync --frozen --no-dev --no-install-project --project services
# Chromium for the Playwright fallback of downloader (before src, so code changes keep this layer).
RUN playwright install --with-deps chromium && rm -rf /var/lib/apt/lists/*
COPY core/src data_pipeline/src
COPY server/src services/src
RUN uv sync --frozen --no-dev --project services

COPY core/config data_pipeline/config
RUN useradd --create-home --uid 1000 app \
    && mkdir -p /app/data_pipeline/data/enrichment/cache /app/data_pipeline/data/state \
    && chown -R app /app/data_pipeline/data
USER app

# Paths in config/*.yaml are relative to the data_pipeline project, as on the host.
WORKDIR /app/data_pipeline
ENTRYPOINT ["services"]
