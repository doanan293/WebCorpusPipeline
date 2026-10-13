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
ProfileOption = Annotated[Literal["safe-fast"], typer.Option(help="Crawl concurrency profile.")]
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
def run(  # noqa: PLR0913, PLR0912 -- one parameter per documented crawl flag
    input_path: Annotated[
        Path, typer.Option("--input", "-i", help="Path to the canonical URLs file.")
    ],
    output: Annotated[Path, typer.Option("--output", "-o", help="Crawl output directory.")],
    assets: AssetsOption = None,
    reuse_content_from: Annotated[
        Path | None,
        typer.Option(help="Reuse a completed content-only crawl and download only its assets."),
    ] = None,
    max_query_variants_per_path: Annotated[
        int | None, typer.Option(help="Maximum query parameter variants per path (default: 20).")
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
    profile: ProfileOption = "safe-fast",
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
    retry_truncated: Annotated[
        bool,
        typer.Option(
            "--retry-truncated",
            help="Requeue recoverable query-variant truncations while resuming.",
        ),
    ] = False,
    retry_access_gates: Annotated[
        bool,
        typer.Option(
            "--retry-access-gates",
            help="Requeue pages previously skipped as CAPTCHA-blocked while resuming.",
        ),
    ] = False,
    retry_policy_skips: Annotated[
        bool,
        typer.Option(
            "--retry-policy-skips",
            help="Migrate access policy and requeue recoverable policy skips while resuming.",
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
    from data_pipeline.crawl.config import CrawlerConfig
    from data_pipeline.crawl.frontier.seeds import parse_seed_file
    from data_pipeline.crawl.options import crawl_options_from_config
    from data_pipeline.crawl.run import full as run_full
    from data_pipeline.crawl.runtime.profiles import resolve_runtime_tuning

    if refresh and (
        resume
        or reuse_content_from is not None
        or retry_failed
        or retry_truncated
        or retry_access_gates
        or retry_policy_skips
    ):
        return errors.fail(
            "--refresh cannot be combined with --resume, --reuse-content-from or --retry-* flags"
        )
    if reuse_content_from is not None and assets not in (None, "all"):
        return errors.fail("--reuse-content-from requires --assets all")
    for enabled, flag in (
        (retry_failed, "--retry-failed"),
        (retry_truncated, "--retry-truncated"),
        (retry_access_gates, "--retry-access-gates"),
        (retry_policy_skips, "--retry-policy-skips"),
    ):
        if enabled and not resume:
            return errors.fail(f"{flag} requires --resume")

    if not input_path.is_file():
        return errors.fail(f"input file not found: {input_path}")

    try:
        seeds = parse_seed_file(input_path)
    except Exception as exc:  # noqa: BLE001 -- any malformed seed file is a usage error
        return errors.fail(f"reading seed file: {exc}")

    config_path = context.pick(config, context.current_paths().crawler_config)
    try:
        crawler_config = CrawlerConfig.load(config_path, hostnames=seeds.allowed_hostnames)
    except Exception as exc:  # noqa: BLE001 -- any invalid configuration is a usage error
        return errors.fail(f"loading configuration: {exc}")

    if reuse_content_from is not None and crawler_config.assets != "all" and assets is None:
        return errors.fail("--reuse-content-from requires --assets all")

    options = crawl_options_from_config(
        crawler_config,
        assets=assets,
        max_file_bytes=max_file_bytes,
        max_total_file_bytes=max_total_file_bytes,
        max_query_variants_per_path=max_query_variants_per_path,
        pagination_empty_pages=pagination_empty_pages,
        max_urls_per_host=max_urls_per_host,
        max_total_urls=max_total_urls,
    )
    tuning = resolve_runtime_tuning(
        config=crawler_config,
        profile=profile,
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
        retry_truncated=retry_truncated,
        retry_access_gates=retry_access_gates,
        retry_policy_skips=retry_policy_skips,
        reuse_content_from=reuse_content_from,
        refresh=refresh,
    )


@app.command()
def discover(
    input_path: Annotated[Path, typer.Option("--input", "-i", help="Path to the seeds file.")],
    output: Annotated[Path, typer.Option("--output", "-o", help="Discovery output directory.")],
    profile: ProfileOption = "safe-fast",
    concurrency: ConcurrencyOption = None,
    max_per_host: PerHostOption = None,
    resource_limit_percent: ResourceLimitOption = None,
    config: ConfigOption = None,
    resume: Annotated[
        bool, typer.Option("--resume", help="Resume an interrupted discovery run.")
    ] = False,
) -> int:
    """Sitemap-only URL discovery for mixed hostname and exact URL seeds."""
    from data_pipeline.crawl.config import CrawlerConfig
    from data_pipeline.crawl.frontier.seeds import parse_seed_file
    from data_pipeline.crawl.run import discovery as run_discovery
    from data_pipeline.crawl.runtime.profiles import resolve_runtime_tuning

    if not input_path.is_file():
        return errors.fail(f"input file not found: {input_path}")
    try:
        seeds = parse_seed_file(input_path)
    except ValueError as exc:
        return errors.fail(f"reading seeds: {exc}")

    config_path = context.pick(config, context.current_paths().crawler_config)
    try:
        crawler_config = CrawlerConfig.load(config_path, hostnames=seeds.allowed_hostnames)
    except Exception as exc:  # noqa: BLE001 -- any invalid configuration is a usage error
        return errors.fail(f"loading configuration: {exc}")

    tuning = resolve_runtime_tuning(
        config=crawler_config,
        profile=profile,
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
    )
