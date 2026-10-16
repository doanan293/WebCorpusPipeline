from __future__ import annotations

import sqlite3
import subprocess
import sys

from data_pipeline.domain.shared.errors import ExternalFailure

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
}


def fail(message: str, *, exit_code: int = 2) -> int:
    """Print ``Error: <message>`` and return ``exit_code`` (2: usage error, 1: external failure)."""
    print(f"Error: {message}", file=sys.stderr)
    return exit_code


# Failures of outside systems (MongoDB, a browser, SQLite) are reported, not shown as tracebacks.
_EXTERNAL_ERROR_MODULES = ("pymongo", "playwright", "sqlite3")


def external_failure_message(exc: Exception) -> str | None:
    """One line describing a failure of an outside system, or None for a program bug."""
    if isinstance(exc, ExternalFailure):
        return str(exc)
    if isinstance(exc, subprocess.CalledProcessError):
        detail = (exc.stderr or exc.stdout or "").strip() if isinstance(exc.stderr, str) else ""
        command = " ".join(str(part) for part in exc.cmd) if isinstance(exc.cmd, list) else exc.cmd
        return f"{command} exited with {exc.returncode}: {detail}".strip()
    if isinstance(exc, sqlite3.Error) or type(exc).__module__.startswith(_EXTERNAL_ERROR_MODULES):
        first_line = (str(exc).splitlines() or [""])[0]
        return f"{type(exc).__name__}: {first_line}"
    return None


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
