from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from pathlib import Path
from typing import Annotated

import typer

from crawler import bootstrap
from crawler.cli import context
from crawler.cli.pipeline import crawl as pipeline
from crawler.domain.merger.config import MergerConfig
from crawler.domain.merger.seeds import Seeds, parse_seeds
from crawler.domain.shared.config import ProjectPaths

app = typer.Typer(help="Crawl with the five blocks in one process.", no_args_is_help=True)
seeds_app = typer.Typer(help="Where crawls start and which hosts they spread over.")


def under_root(paths: ProjectPaths, path: Path) -> Path:
    return path if path.is_absolute() else paths.root / path


def merger_settings(paths: ProjectPaths) -> MergerConfig:
    config = bootstrap.merger_config(paths.root / "config" / "merger.yaml")
    return MergerConfig(
        port=config.port,
        seeds_file=under_root(paths, config.seeds_file),
        initial_scope_file=under_root(paths, config.initial_scope_file),
        batch_size=config.batch_size,
        batch_seconds=config.batch_seconds,
        outbox_retry_seconds=config.outbox_retry_seconds,
        database=under_root(paths, config.database),
        outbox=under_root(paths, config.outbox),
    )


def build_parts(paths: ProjectPaths, merger: MergerConfig, database: Path) -> pipeline.CrawlParts:
    downloader = bootstrap.downloader_config(paths.root / "config" / "downloader.yaml")
    reader = bootstrap.reader_config(paths.root / "config" / "reader.yaml")
    settings = bootstrap.load_settings(paths)
    return pipeline.CrawlParts(
        queue=bootstrap.url_queue(database),
        state=bootstrap.merger_state(merger.database),
        outbox=bootstrap.outbox(merger.outbox),
        store=bootstrap.corpus_store(settings.mongo_uri, settings.mongo_database),
        getter=bootstrap.page_getter(downloader),
        renderer=bootstrap.renderer(downloader),
        readers=bootstrap.document_readers(reader, paths),
        clock=bootstrap.system_clock(),
        now_iso=bootstrap.utc_now_iso,
        merger=merger,
    )


def close_parts(parts: pipeline.CrawlParts) -> None:
    parts.store.close()
    parts.queue.close()
    parts.state.close()


def initial_scope(merger: MergerConfig) -> frozenset[str]:
    path = merger.initial_scope_file
    return bootstrap.hostnames(path) if path.exists() else frozenset()


def _check(seeds: Seeds) -> Seeds:
    for problem in seeds.problems:
        typer.echo(f"Warning: seeds {problem}", err=True)
    return seeds


@app.command("run")
def run(
    seeds: Annotated[
        Path | None,
        typer.Option(
            help="Seeds file, one host or URL per line (default: seeds_file of merger.yaml)."
        ),
    ] = None,
    forever: Annotated[
        bool, typer.Option(help="Keep running (recrawls included) until Ctrl+C, like the services.")
    ] = False,
    config: Annotated[
        Path | None, typer.Option(help="Pace of this crawl (default: config/crawl.yaml).")
    ] = None,
) -> int:
    """Crawl from the seeds until nothing is due, continuing the shared state of the services."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    paths = context.current_paths()
    crawl_config = bootstrap.crawl_config(
        context.pick(config, paths.root / "config" / "crawl.yaml"),
        paths.root / "config" / "mediator.yaml",
    )
    merger = merger_settings(paths)
    database = under_root(paths, crawl_config.mediator.database)
    start = _check(bootstrap.seeds(context.pick(seeds, merger.seeds_file)))
    initial = initial_scope(merger)
    with (
        bootstrap.state_lease(database, "crawler crawl"),
        bootstrap.state_lease(merger.database, "crawler crawl"),
    ):
        parts = build_parts(paths, merger, database)

        async def go() -> pipeline.CrawlSummary:
            stop = asyncio.Event()
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                with contextlib.suppress(NotImplementedError, RuntimeError):
                    loop.add_signal_handler(sig, stop.set)
            try:
                return await pipeline.crawl(
                    crawl_config,
                    parts,
                    seeds=start,
                    initial_scope=initial,
                    forever=forever,
                    stop=stop,
                )
            finally:
                close = getattr(parts.getter, "close", None)
                if close is not None:
                    await close()

        try:
            summary = asyncio.run(go())
        finally:
            close_parts(parts)
    print(
        f"fetched {summary.sent}, stored {summary.stored}, failed {summary.failed}, "
        f"{summary.queued_due} urls still due"
    )
    return 0


@seeds_app.command("add")
def add(
    entries: Annotated[list[str], typer.Argument(help="Hostnames or http(s) URLs.")],
) -> int:
    """Add seeds now: their hosts join the scope and they are queued (known URLs keep theirs)."""
    paths = context.current_paths()
    seeds = parse_seeds(entries)
    if seeds.problems:
        msg = "; ".join(seeds.problems)
        raise ValueError(msg)
    crawl_config = bootstrap.crawl_config(
        paths.root / "config" / "crawl.yaml", paths.root / "config" / "mediator.yaml"
    )
    merger = merger_settings(paths)
    database = under_root(paths, crawl_config.mediator.database)
    with (
        bootstrap.state_lease(database, "crawler seeds"),
        bootstrap.state_lease(merger.database, "crawler seeds"),
    ):
        parts = build_parts(paths, merger, database)
        try:
            new = asyncio.run(pipeline.add_seeds(parts, seeds, initial_scope(merger)))
        finally:
            close_parts(parts)
    known = len(seeds.urls) - new
    print(f"{new} new urls queued, {known} already known; seeds cover {len(seeds.hosts)} hosts")
    return 0
