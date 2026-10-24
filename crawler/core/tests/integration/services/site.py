"""A small local website for the end-to-end test: pages, a duplicate, a PDF and a DOCX."""

from __future__ import annotations

import asyncio
import io
from collections import Counter

import docx
import pymupdf
from aiohttp import web


def _page(title: str, text: str, links: list[tuple[str, str]]) -> str:
    anchors = "".join(f'<p><a href="{href}">{label}</a></p>' for href, label in links)
    return (
        f"<html><head><title>{title}</title></head><body><article><h1>{title}</h1>"
        f"<p>{text}</p>{anchors}</article></body></html>"
    )


def _pdf() -> bytes:
    document = pymupdf.open()
    document.new_page().insert_text((72, 72), "Thong bao tuyen sinh PDF")
    return document.tobytes()


def _docx() -> bytes:
    document = docx.Document()
    document.add_paragraph("Bao cao nam hoc DOCX")
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


# What crawling the site from "/" must store, whichever way the blocks run (paths on the site).
EXPECTED_HTML_PATHS = frozenset({"/", "/a", "/b"})
EXPECTED_OTHER_PATHS = frozenset({"/doc.pdf", "/report.docx"})
EXPECTED_EDGE = ("/", "/a", "Bai A")


def site_app(hits: Counter[str] | None = None, slow_seconds: float = 30.0) -> web.Application:
    """The site; ``hits`` counts requests per path; ``/slow`` answers after ``slow_seconds``."""
    pages = {
        "/": _page(
            "Trang chu",
            "Dai hoc Bach khoa Ha Noi trang chu.",
            [
                ("/a", "Bai A"),
                ("/b", "Bai B"),
                ("/doc.pdf", "PDF"),
                ("/report.docx", "DOCX"),
                ("https://google.com/", "Google"),
            ],
        ),
        "/a": _page("Bai", "Noi dung bai viet giong nhau.", []),
        "/b": _page("Bai", "Noi dung bai viet giong nhau.", []),
        "/slow": _page("Cham", "Trang tra loi cham.", []),
    }
    files = {
        "/doc.pdf": (_pdf(), "application/pdf"),
        "/report.docx": (
            _docx(),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
    }

    async def handle(request: web.Request) -> web.Response:
        if hits is not None:
            hits[request.path] += 1
        if request.path == "/slow":
            await asyncio.sleep(slow_seconds)
        if request.path in pages:
            return web.Response(text=pages[request.path], content_type="text/html", charset="utf-8")
        if request.path in files:
            body, content_type = files[request.path]
            return web.Response(body=body, content_type=content_type)
        raise web.HTTPNotFound

    app = web.Application()
    app.router.add_get("/{tail:.*}", handle)
    return app
