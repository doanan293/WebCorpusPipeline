from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

import typer

from data_pipeline.cli import context, errors

app = typer.Typer(help="Crawl the HUST websites.", no_args_is_help=True)

AssetsOption = Annotated[
    Literal["content-only", "all"] | None,
    typer.Option(
        help="Download directly referenced semantic assets; default records references only."
    ),
]
ConfigOption = Annotated[
    Path | None, typer.Option(help="Crawler YAML (default: config/crawler.yaml in the project).")
]
ConcurrencyOption = Annotated[
    int | None, typer.Option(help="Global concurrency limit (overrides YAML).")
]
PerHostOption = Annotated[
    int | None, typer.Option(help="Per-host concurrency limit (overrides YAML).")
]
ResourceLimitOption = Annotated[
    int | None, typer.Option(help="CPU/RAM utilization threshold (overrides YAML).")
]


@app.command()
def run(
    input_path: Annotated[
        Path, typer.Option("--input", "-i", help="Path to the canonical URLs file.")
    ],
    output: Annotated[Path, typer.Option("--output", "-o", help="Crawl output directory.")],
    assets: AssetsOption = None,
    reuse_content_from: Annotated[
        Path | None,
        typer.Option(help="Reuse a completed content-only crawl and download only its assets."),
    ] = None,
    pagination_empty_pages: Annotated[
        int | None,
        typer.Option(help="Consecutive empty pagination pages before stopping (default: 3)."),
    ] = None,
    max_urls_per_host: Annotated[
        int | None, typer.Option(help="Maximum URLs crawled per hostname (default: 100000).")
    ] = None,
    max_total_urls: Annotated[
        int | None, typer.Option(help="Maximum total URLs across all hosts (default: 1000000).")
    ] = None,
    max_file_bytes: Annotated[
        int | None, typer.Option(help="Maximum single file size in bytes (default: 100 MiB).")
    ] = None,
    max_total_file_bytes: Annotated[
        int | None, typer.Option(help="Maximum total file storage in bytes (default: 100 GiB).")
    ] = None,
    concurrency: ConcurrencyOption = None,
    max_per_host: PerHostOption = None,
    resource_limit_percent: ResourceLimitOption = None,
    config: ConfigOption = None,
    resume: Annotated[
        bool, typer.Option("--resume", help="Resume an interrupted crawl run.")
    ] = False,
    retry_failed: Annotated[
        bool,
        typer.Option("--retry-failed", help="Retry transient network failures while resuming."),
    ] = False,
    retry_access_gates: Annotated[
        bool,
        typer.Option(
            "--retry-access-gates",
            help="Requeue pages previously skipped behind a CAPTCHA or login gate while resuming.",
        ),
    ] = False,
    refresh: Annotated[
        bool,
        typer.Option(
            "--refresh",
            help="Refetch hubs and recent pages of a finished crawl and crawl only new URLs.",
        ),
    ] = False,
) -> int:
    """Crawl hostname and exact-URL seeds with the unified hybrid crawler."""
    from data_pipeline.crawl.application import run_crawl as run_full
    from data_pipeline.crawl.domain.options import crawl_options_from_config
    from data_pipeline.crawl.domain.tuning import resolve_runtime_tuning
    from data_pipeline.crawl.infrastructure.config_file import load_crawler_config
    from data_pipeline.crawl.infrastructure.scrapy.runner import ScrapyEngine
    from data_pipeline.crawl.infrastructure.seeds_file import parse_seed_file
    from data_pipeline.crawl.infrastructure.sqlite.storage import SqliteCrawlStorage

    run_full.validate_mode_flags(
        resume=resume,
        retry_failed=retry_failed,
        retry_access_gates=retry_access_gates,
        reuse_content_from=reuse_content_from,
        refresh=refresh,
    )
    if reuse_content_from is not None and assets not in (None, "all"):
        return errors.fail("--reuse-content-from requires --assets all")

    if not input_path.is_file():
        return errors.fail(f"input file not found: {input_path}")

    try:
        seeds = parse_seed_file(input_path)
    except Exception as exc:  # noqa: BLE001 -- any unreadable seed file or configuration is a usage error
        return errors.fail(f"reading seed file: {exc}")

    config_path = context.pick(config, context.current_paths().crawler_config)
    try:
        crawler_config = load_crawler_config(config_path, hostnames=seeds.allowed_hostnames)
    except Exception as exc:  # noqa: BLE001 -- any unreadable seed file or configuration is a usage error
        return errors.fail(f"loading configuration: {exc}")

    if reuse_content_from is not None and crawler_config.assets != "all" and assets is None:
        return errors.fail("--reuse-content-from requires --assets all")

    options = crawl_options_from_config(
        crawler_config,
        assets=assets,
        max_file_bytes=max_file_bytes,
        max_total_file_bytes=max_total_file_bytes,
        pagination_empty_pages=pagination_empty_pages,
        max_urls_per_host=max_urls_per_host,
        max_total_urls=max_total_urls,
    )
    tuning = resolve_runtime_tuning(
        config=crawler_config,
        concurrency=concurrency,
        max_per_host=max_per_host,
        resource_limit_percent=resource_limit_percent,
    )
    return run_full.run_crawl(
        input_path=input_path,
        output=output,
        config=crawler_config,
        seeds=seeds,
        options=options,
        tuning=tuning,
        resume=resume,
        retry_failed=retry_failed,
        retry_access_gates=retry_access_gates,
        reuse_content_from=reuse_content_from,
        refresh=refresh,
        engine=ScrapyEngine(),
        storage=SqliteCrawlStorage(),
    )


@app.command()
def discover(
    input_path: Annotated[Path, typer.Option("--input", "-i", help="Path to the seeds file.")],
    output: Annotated[Path, typer.Option("--output", "-o", help="Discovery output directory.")],
    concurrency: ConcurrencyOption = None,
    max_per_host: PerHostOption = None,
    resource_limit_percent: ResourceLimitOption = None,
    config: ConfigOption = None,
    resume: Annotated[
        bool, typer.Option("--resume", help="Resume an interrupted discovery run.")
    ] = False,
) -> int:
    """Sitemap-only URL discovery for mixed hostname and exact URL seeds."""
    from data_pipeline.crawl.application import run_discovery
    from data_pipeline.crawl.domain.tuning import resolve_runtime_tuning
    from data_pipeline.crawl.infrastructure.config_file import load_crawler_config
    from data_pipeline.crawl.infrastructure.scrapy.runner import ScrapyEngine
    from data_pipeline.crawl.infrastructure.seeds_file import parse_seed_file
    from data_pipeline.crawl.infrastructure.sqlite.storage import SqliteCrawlStorage

    if not input_path.is_file():
        return errors.fail(f"input file not found: {input_path}")
    try:
        seeds = parse_seed_file(input_path)
    except ValueError as exc:
        return errors.fail(f"reading seeds: {exc}")

    config_path = context.pick(config, context.current_paths().crawler_config)
    try:
        crawler_config = load_crawler_config(config_path, hostnames=seeds.allowed_hostnames)
    except Exception as exc:  # noqa: BLE001 -- any unreadable seed file or configuration is a usage error
        return errors.fail(f"loading configuration: {exc}")

    tuning = resolve_runtime_tuning(
        config=crawler_config,
        concurrency=concurrency,
        max_per_host=max_per_host,
        resource_limit_percent=resource_limit_percent,
    )
    return run_discovery.run_discovery(
        input_path=input_path,
        output=output,
        config=crawler_config,
        seeds=seeds,
        tuning=tuning,
        resume=resume,
        engine=ScrapyEngine(),
        storage=SqliteCrawlStorage(),
    )


@app.command()
def reextract(
    output: Annotated[
        Path, typer.Option("--output", "-o", help="Finished crawl output directory.")
    ],
) -> int:
    """Re-run extraction on the stored raw HTML of a finished crawl and rebuild final/ (offline)."""
    from data_pipeline.crawl.application import reextract as run_reextract
    from data_pipeline.crawl.infrastructure.sqlite.storage import SqliteCrawlStorage

    summary = run_reextract.reextract(output, SqliteCrawlStorage())
    print(
        f"reextract: pages={summary.pages} changed={summary.changed} "
        f"duplicates={summary.duplicates}"
    )
    return 0
