from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from pathlib import Path
from typing import Annotated

import typer

from data_pipeline import bootstrap
from data_pipeline.cli import context
from data_pipeline.domain.aggregator.config import AggregatorConfig
from data_pipeline.domain.aggregator.seeds import Seeds, parse_seeds
from data_pipeline.domain.shared.config import ProjectPaths
from data_pipeline.runner import crawl as runner

app = typer.Typer(help="Crawl with the five blocks in one process.", no_args_is_help=True)
seeds_app = typer.Typer(help="Where crawls start and which hosts they spread over.")


def _under_root(paths: ProjectPaths, path: Path) -> Path:
    return path if path.is_absolute() else paths.root / path


def _aggregator(paths: ProjectPaths) -> AggregatorConfig:
    config = bootstrap.aggregator_config(paths.root / "config" / "aggregator.yaml")
    return AggregatorConfig(
        port=config.port,
        seeds_file=_under_root(paths, config.seeds_file),
        initial_scope_file=_under_root(paths, config.initial_scope_file),
        batch_size=config.batch_size,
        batch_seconds=config.batch_seconds,
        outbox_retry_seconds=config.outbox_retry_seconds,
        database=_under_root(paths, config.database),
        outbox=_under_root(paths, config.outbox),
    )


def _parts(paths: ProjectPaths, aggregator: AggregatorConfig, database: Path) -> runner.CrawlParts:
    fetcher = bootstrap.fetcher_config(paths.root / "config" / "fetcher.yaml")
    parser = bootstrap.parser_config(paths.root / "config" / "parser.yaml")
    settings = bootstrap.load_settings(paths)
    return runner.CrawlParts(
        queue=bootstrap.url_queue(database),
        state=bootstrap.aggregator_state(aggregator.database),
        outbox=bootstrap.outbox(aggregator.outbox),
        store=bootstrap.corpus_store(settings.mongo_uri, settings.mongo_database),
        getter=bootstrap.page_getter(fetcher),
        renderer=bootstrap.renderer(fetcher),
        readers=bootstrap.document_readers(parser, paths),
        clock=bootstrap.system_clock(),
        now_iso=bootstrap.utc_now_iso,
        aggregator=aggregator,
    )


def _check(seeds: Seeds) -> Seeds:
    for problem in seeds.problems:
        typer.echo(f"Warning: seeds {problem}", err=True)
    return seeds


@app.command("run")
def run(
    seeds: Annotated[
        Path | None,
        typer.Option(
            help="Seeds file, one host or URL per line (default: seeds_file of aggregator.yaml)."
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
        paths.root / "config" / "controller.yaml",
    )
    aggregator = _aggregator(paths)
    database = _under_root(paths, crawl_config.controller.database)
    start = _check(bootstrap.seeds(context.pick(seeds, aggregator.seeds_file)))
    initial_file = aggregator.initial_scope_file
    initial = bootstrap.hostnames(initial_file) if initial_file.exists() else frozenset()
    with (
        bootstrap.state_lease(database, "data-pipeline crawl"),
        bootstrap.state_lease(aggregator.database, "data-pipeline crawl"),
    ):
        parts = _parts(paths, aggregator, database)

        async def go() -> runner.CrawlSummary:
            stop = asyncio.Event()
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                with contextlib.suppress(NotImplementedError, RuntimeError):
                    loop.add_signal_handler(sig, stop.set)
            try:
                return await runner.crawl(
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
            parts.store.close()
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
        paths.root / "config" / "crawl.yaml", paths.root / "config" / "controller.yaml"
    )
    aggregator = _aggregator(paths)
    database = _under_root(paths, crawl_config.controller.database)
    with (
        bootstrap.state_lease(database, "data-pipeline seeds"),
        bootstrap.state_lease(aggregator.database, "data-pipeline seeds"),
    ):
        parts = _parts(paths, aggregator, database)
        try:
            new = asyncio.run(runner.add_seeds(parts, seeds))
        finally:
            parts.store.close()
    known = len(seeds.urls) - new
    print(f"{new} new urls queued, {known} already known; seeds cover {len(seeds.hosts)} hosts")
    return 0
