from __future__ import annotations

import asyncio
import logging

from crawler.application.reader.parse import charset_of
from crawler.application.reader.ports import MergerClient, RawPages
from crawler.domain.reader.extract.article import analyze_article
from crawler.domain.reader.records import html_record

log = logging.getLogger(__name__)


async def reextract(pages: RawPages, merger: MergerClient) -> int:
    """Run the current extractor over the stored HTML and send each record to the merger.

    Records go under the URL the page was served from (after redirects), as the downloader sends
    them.
    """
    sent = 0
    for page in pages.iter():
        analysis = await asyncio.to_thread(
            analyze_article, page.body, page.final_url, charset_of(page.content_type)
        )
        if await merger.send(html_record(page.final_url, analysis)):
            sent += 1
        else:
            log.warning("merger refused %s", page.final_url)
    return sent
