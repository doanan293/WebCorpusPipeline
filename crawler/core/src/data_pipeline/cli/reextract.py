from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Annotated

import typer

from data_pipeline import bootstrap
from data_pipeline.cli import context
from data_pipeline.cli import crawl as crawl_cli
from data_pipeline.runner import crawl as runner


def reextract(
    crawl: Annotated[
        Path | None,
        typer.Option(help="Batch crawl output holding state/ and html/ (default: data/crawl)."),
    ] = None,
    merger: Annotated[
        str | None,
        typer.Option(help="Send the records to this merger service instead of handling them here."),
    ] = None,
) -> int:
    """Run the current extractor over the stored HTML and store the records again.

    Without --merger the records go through the blocks in this process, on the shared state
    (like `crawl run`); with it they are posted to that merger service.
    """
    from data_pipeline.application.reader import reextract as use_case

    paths = context.current_paths()
    pages = bootstrap.raw_pages(context.pick(crawl, paths.root / "data/crawl"))
    if merger is not None:
        client = bootstrap.merger_client(merger)

        async def post() -> int:
            try:
                return await use_case.reextract(pages, client)
            finally:
                await client.close()

        print(f"sent {asyncio.run(post())} records")
        return 0

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    crawl_config = bootstrap.crawl_config(
        paths.root / "config" / "crawl.yaml", paths.root / "config" / "mediator.yaml"
    )
    settings = crawl_cli.merger_settings(paths)
    database = crawl_cli.under_root(paths, crawl_config.mediator.database)
    with (
        bootstrap.state_lease(database, "data-pipeline reextract"),
        bootstrap.state_lease(settings.database, "data-pipeline reextract"),
    ):
        parts = crawl_cli.build_parts(paths, settings, database)
        try:
            initial = crawl_cli.initial_scope(settings)
            sent = asyncio.run(runner.reextract_here(parts, pages, initial))
        finally:
            crawl_cli.close_parts(parts)
    print(f"stored {sent} records again")
    return 0
