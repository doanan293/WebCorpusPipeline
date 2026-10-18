from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

import typer

from data_pipeline import bootstrap
from data_pipeline.cli import context


def reextract(
    crawl: Annotated[
        Path | None,
        typer.Option(help="Batch crawl output holding state/ and html/ (default: data/crawl)."),
    ] = None,
    aggregator: Annotated[
        str, typer.Option(help="Aggregator block to send the records to.")
    ] = "http://127.0.0.1:3300",
) -> int:
    """Run the current extractor over the stored HTML and send the records to the aggregator."""
    from data_pipeline.application.parser import reextract as use_case

    pages = bootstrap.raw_pages(context.pick(crawl, context.current_paths().root / "data/crawl"))
    client = bootstrap.aggregator_client(aggregator)

    async def run() -> int:
        try:
            return await use_case.reextract(pages, client)
        finally:
            await client.close()

    print(f"sent {asyncio.run(run())} records")
    return 0
