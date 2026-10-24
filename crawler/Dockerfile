# One image for every block of the slide; each container runs `crawler serve <block>`.
# Build context is crawler/, the project folder.
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
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
# Chromium for the Playwright fallback of downloader (before src, so code changes keep this layer).
RUN playwright install --with-deps chromium && rm -rf /var/lib/apt/lists/*
COPY src src
RUN uv sync --frozen --no-dev

COPY config config
RUN useradd --create-home --uid 1000 app \
    && mkdir -p /app/data/enrichment/cache /app/data/state \
    && chown -R app /app/data
USER app

# Paths in config/*.yaml are relative to the project root, as on the host.
ENTRYPOINT ["crawler"]
