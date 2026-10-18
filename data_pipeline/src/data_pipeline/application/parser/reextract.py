from __future__ import annotations

import asyncio
import logging

from data_pipeline.application.parser.parse import charset_of
from data_pipeline.application.parser.ports import AggregatorClient, RawPages
from data_pipeline.domain.parser.extract.article import analyze_article
from data_pipeline.domain.parser.records import html_record

log = logging.getLogger(__name__)


async def reextract(pages: RawPages, aggregator: AggregatorClient) -> int:
    """Run the current extractor over the stored HTML and send each record to the aggregator."""
    sent = 0
    for page in pages.iter():
        analysis = await asyncio.to_thread(
            analyze_article, page.body, page.final_url, charset_of(page.content_type)
        )
        if await aggregator.send(html_record(page.url, analysis)):
            sent += 1
        else:
            log.warning("aggregator refused %s", page.url)
    return sent
