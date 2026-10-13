from __future__ import annotations

import sys

_EXTRA_FOR_MODULE = {
    "scrapy": "crawl",
    "scrapy_playwright": "crawl",
    "playwright": "crawl",
    "lxml": "crawl",
    "aiohttp": "enrich",
    "fitz": "enrich",
    "pymupdf": "enrich",
    "docx": "enrich",
    "openpyxl": "enrich",
    "xlrd": "enrich",
    "pptx": "enrich",
    "llama_cloud": "enrich",
    "filelock": "enrich",
}


def fail(message: str) -> int:
    """Print ``Error: <message>`` and return the usage-error exit code."""
    print(f"Error: {message}", file=sys.stderr)
    return 2


def missing_extra_message(exc: ModuleNotFoundError) -> str | None:
    """Explain which optional dependency group provides the module that failed to import."""
    module = (exc.name or "").split(".")[0]
    extra = _EXTRA_FOR_MODULE.get(module)
    if extra is None:
        return None
    return (
        f"this command needs the '{extra}' extra ({module} is not installed); "
        f"run: uv sync --extra {extra}"
    )
