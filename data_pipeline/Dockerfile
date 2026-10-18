# One image for every block of the slide; each container runs `data-pipeline serve <block>`.
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
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --extra enrich --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --extra enrich
# Chromium for the Playwright fallback of Tải về.
RUN playwright install --with-deps chromium && rm -rf /var/lib/apt/lists/*

COPY config ./config
RUN useradd --create-home --uid 1000 app \
    && mkdir -p /state /app/data/enrichment/cache \
    && chown -R app /state /app/data
USER app

ENTRYPOINT ["data-pipeline"]
