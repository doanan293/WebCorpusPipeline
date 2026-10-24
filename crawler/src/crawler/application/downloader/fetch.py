from __future__ import annotations

import dataclasses
from http import HTTPStatus
from typing import Literal

from crawler.application.downloader.ports import PageGetter, ReaderClient, Renderer
from crawler.domain.downloader.access import classify_access
from crawler.domain.downloader.formats import payload_format
from crawler.domain.downloader.models import Fetched, FetchError, Validators

type FetchOutcome = Literal["forwarded", "not_modified", "blocked", "skipped", "failed", "unsent"]


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
    url: str,
    getter: PageGetter,
    renderer: Renderer,
    reader: ReaderClient,
    validators: Validators | None = None,
) -> FetchOutcome:
    """Download one URL and pass it to the reader block, rendering JS-only pages first.

    A conditional request answered 304 is passed on as ``not_modified``, under ``url``.
    """
    try:
        page = await getter.get(url, validators)
    except FetchError:
        return "failed"
    if page.status == HTTPStatus.NOT_MODIFIED:
        return "not_modified" if await reader.send_not_modified(url) else "unsent"
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
            fetched = page
            try:
                page = await renderer.render(page.final_url)
            except FetchError:
                return "failed"
            # The browser's copy has no HTTP validators; keep those of the plain download.
            page = dataclasses.replace(page, etag=fetched.etag, last_modified=fetched.last_modified)
            access = classify_access(
                status=page.status, url=page.final_url, html=_html_text(page), rendered=True
            )
            if access.outcome != "public":
                return "blocked"
    return "forwarded" if await reader.send(page, fmt, requested_url=url) else "unsent"
