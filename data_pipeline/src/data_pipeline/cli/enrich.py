from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated

import typer

from data_pipeline import bootstrap
from data_pipeline.cli import context
from data_pipeline.cli.context import (
    JsonOption,
    RebindOption,
    SourceOption,
    WorkDirOption,
)
from data_pipeline.domain.enrich.models import RunSummary, jsonable

app = typer.Typer(help="Download crawled documents and extract their text.", no_args_is_help=True)


def _summary(summary: RunSummary, as_json: bool) -> int:
    value = jsonable(summary)
    if as_json:
        print(json.dumps(value, ensure_ascii=False, indent=2))
    else:
        print(
            "downloaded={downloaded} parsed={parsed} failed={failed} pending={pending} "
            "quota_blocked={quota_blocked} cloud_failed={cloud_failed}".format(**value)
        )
    unfinished = summary.pending + summary.quota_blocked + summary.cloud_failed
    return 4 if unfinished else 0


@app.command()
def inventory(
    source: SourceOption = None,
    work_dir: WorkDirOption = None,
    rebind_source: RebindOption = False,
) -> int:
    """Index the document links found in the crawl output."""
    from data_pipeline.application.enrich import inventory as enrich_inventory

    settings = context.build_settings(source, work_dir)
    with bootstrap.work_state(settings, rebind=rebind_source) as uow:
        summary = enrich_inventory.index_inventory(
            bootstrap.article_source(settings.source_path), uow.inventory
        )
    print(json.dumps(jsonable(summary), ensure_ascii=False, indent=2))
    return 0


@app.command()
def download(
    source: SourceOption = None,
    work_dir: WorkDirOption = None,
    json_output: JsonOption = False,
    rebind_source: RebindOption = False,
    retry_failed: Annotated[
        bool,
        typer.Option("--retry-failed", help="download the failed documents again from scratch"),
    ] = False,
) -> int:
    """Download the inventoried documents (resumable)."""
    from data_pipeline.application.enrich import download as downloader

    settings = context.build_settings(source, work_dir)
    with bootstrap.work_state(settings, rebind=rebind_source) as uow:
        if retry_failed:
            print(f"requeued {uow.downloads.reset_failed_downloads()} failed downloads")
        asyncio.run(
            downloader.run_downloads(
                uow.downloads,
                uow.failures,
                settings,
                files_root=uow.root / "files",
                transport_factory=bootstrap.http_transport_factory(),
                files=bootstrap.download_files(),
            )
        )
        summary = uow.summary()
    return _summary(summary, json_output)


@app.command()
def extract(
    source: SourceOption = None,
    work_dir: WorkDirOption = None,
    json_output: JsonOption = False,
    rebind_source: RebindOption = False,
    reparse_cloud: Annotated[
        bool,
        typer.Option(
            "--reparse-cloud", help="parse again the documents that already went through LlamaParse"
        ),
    ] = False,
) -> int:
    """Extract plain text from the downloaded documents."""
    from data_pipeline.application.enrich import extract as pipeline

    settings = context.build_settings(source, work_dir)
    api_keys = bootstrap.api_keys(context.current_paths())
    with bootstrap.work_state(settings, rebind=rebind_source) as uow:
        if reparse_cloud:
            print(f"requeued {uow.parses.requeue_cloud_parses()} documents for LlamaParse")
        pipeline.extract_documents(
            uow.parses,
            uow.failures,
            settings,
            api_keys=api_keys,
            local=bootstrap.local_parser(),
            cloud=bootstrap.cloud_parser(uow.parses, settings, api_keys),
            cache=bootstrap.parse_cache(uow.root),
        )
        summary = uow.summary()
    return _summary(summary, json_output)


@app.command()
def run(
    source: SourceOption = None,
    work_dir: WorkDirOption = None,
    json_output: JsonOption = False,
    rebind_source: RebindOption = False,
) -> int:
    """Run inventory (if needed), download and extract in one go."""
    from data_pipeline.application.enrich import download as downloader
    from data_pipeline.application.enrich import extract as pipeline
    from data_pipeline.application.enrich import inventory as enrich_inventory

    settings = context.build_settings(source, work_dir)
    api_keys = bootstrap.api_keys(context.current_paths())
    with bootstrap.work_state(settings, rebind=rebind_source) as uow:
        if not uow.inventory.inventory_done(bootstrap.inventory_key(settings.source_path)):
            enrich_inventory.index_inventory(
                bootstrap.article_source(settings.source_path), uow.inventory
            )
        asyncio.run(
            downloader.run_downloads(
                uow.downloads,
                uow.failures,
                settings,
                files_root=uow.root / "files",
                transport_factory=bootstrap.http_transport_factory(),
                files=bootstrap.download_files(),
            )
        )
        pipeline.extract_documents(
            uow.parses,
            uow.failures,
            settings,
            api_keys=api_keys,
            local=bootstrap.local_parser(),
            cloud=bootstrap.cloud_parser(uow.parses, settings, api_keys),
            cache=bootstrap.parse_cache(uow.root),
        )
        summary = uow.summary()
    return _summary(summary, json_output)


@app.command()
def status(
    source: SourceOption = None,
    work_dir: WorkDirOption = None,
    json_output: JsonOption = False,
    rebind_source: RebindOption = False,
) -> int:
    """Report downloaded, parsed, failed, pending and quota-blocked work."""

    settings = context.build_settings(source, work_dir)
    with bootstrap.work_state(settings, rebind=rebind_source) as uow:
        summary = uow.summary()
    return _summary(summary, json_output)


@app.command()
def export(
    output: Annotated[Path, typer.Option(help="Directory to write the enrichment export to.")],
    source: SourceOption = None,
    work_dir: WorkDirOption = None,
    rebind_source: RebindOption = False,
) -> int:
    """Export the extracted documents of the work directory."""
    from data_pipeline.application.enrich import export as enrich_export

    settings = context.build_settings(source, work_dir)
    with bootstrap.work_state(settings, rebind=rebind_source) as uow:
        enrich_export.export_enrichment(uow.inventory, uow.parses, uow.summary(), output)
    return 0


@app.command("reconcile-job")
def reconcile_job(
    request_id: Annotated[str, typer.Option(help="Cloud request id.")],
    job_id: Annotated[str, typer.Option(help="Cloud job id.")],
    work_dir: WorkDirOption = None,
) -> int:
    """Show the recorded status of a cloud parse job."""

    settings = context.build_settings(None, work_dir)
    status_value = bootstrap.cloud_job_status(settings.work_path, request_id, job_id)
    print(
        json.dumps({"request_id": request_id, "job_id": job_id, "status": status_value}, indent=2)
    )
    return 0
