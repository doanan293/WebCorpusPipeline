from __future__ import annotations

import asyncio
import logging
import os
import sys
from typing import Annotated

import typer

from crawler import bootstrap
from crawler.application.storage.pictures import PictureFetchSettings, fetch_pictures
from crawler.cli import context

app = typer.Typer(help="The images of the picture collection.", no_args_is_help=True)


@app.command()
def fetch(
    workers: Annotated[int, typer.Option(help="Downloads at once, all hosts together.")] = 64,
    per_host: Annotated[int, typer.Option(help="Downloads at once on one host.")] = 2,
    retry_errors: Annotated[
        bool, typer.Option("--retry-errors", help="Try again the images that failed before.")
    ] = False,
) -> int:
    """Download the images still without b64 and store them as base64."""
    if workers <= 0 or per_host <= 0:
        msg = "--workers and --per-host must be positive"
        raise ValueError(msg)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", stream=sys.stderr)
    settings = context.build_settings()
    store = bootstrap.picture_store(
        os.environ.get("MONGODB_URI", settings.mongo_uri),
        os.environ.get("MONGODB_DATABASE", settings.mongo_database),
    )
    getter = bootstrap.image_getter()

    async def run() -> dict[str, int]:
        try:
            counts = await fetch_pictures(
                store,
                getter,
                now_iso=bootstrap.utc_now_iso(),
                settings=PictureFetchSettings(
                    workers=workers, per_host=per_host, retry_errors=retry_errors
                ),
            )
        finally:
            await getter.close()
        return dict(counts)

    counts = asyncio.run(run())
    summary = ", ".join(f"{name} {count}" for name, count in sorted(counts.items()))
    print(summary or "nothing to fetch")
    return 0
