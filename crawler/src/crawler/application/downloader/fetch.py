from __future__ import annotations

from http import HTTPStatus
from typing import Literal

from crawler.application.downloader.ports import PageGetter, ReaderClient, Renderer
from crawler.domain.downloader.access import classify_access
from crawler.domain.downloader.formats import payload_format
from crawler.domain.downloader.models import Fetched, FetchError

type FetchOutcome = Literal["forwarded", "blocked", "skipped", "failed", "unsent"]


def _html_text(page: Fetched) -> str:
    charset = "utf-8"
    for part in page.content_type.split(";")[1:]:
        key, _, value = part.strip().partition("=")
        if key.lower() == "charset" and value:
            charset = value.strip("\"'")
    try:
        return page.body.decode(charset, errors="replace")
    except LookupError:
        return page.body.decode("utf-8", errors="replace")


async def fetch_and_forward(
    url: str, getter: PageGetter, renderer: Renderer, reader: ReaderClient
) -> FetchOutcome:
    """Download one URL and pass it to the reader block, rendering JS-only pages first."""
    try:
        page = await getter.get(url)
    except FetchError:
        return "failed"
    if page.status >= HTTPStatus.BAD_REQUEST:
        return "failed"
    fmt = payload_format(page.final_url, page.content_type)
    if fmt is None:
        return "skipped"
    if fmt == "html":
        access = classify_access(
            status=page.status, url=page.final_url, html=_html_text(page), rendered=False
        )
        if access.outcome != "public":
            return "blocked"
        if access.use_playwright:
            try:
                page = await renderer.render(page.final_url)
            except FetchError:
                return "failed"
            access = classify_access(
                status=page.status, url=page.final_url, html=_html_text(page), rendered=True
            )
            if access.outcome != "public":
                return "blocked"
    return "forwarded" if await reader.send(page, fmt) else "unsent"
