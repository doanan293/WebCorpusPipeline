from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated

import typer

from data_pipeline.cli import context
from data_pipeline.cli.context import JsonOption, RebindOption, SourceOption, WorkDirOption
from data_pipeline.enrich.domain.models import RunSummary, jsonable

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
    from data_pipeline.enrich.application import inventory as enrich_inventory
    from data_pipeline.enrich.application import session

    settings = context.build_settings(source, work_dir)
    with session.open_work_state(settings, rebind=rebind_source) as state:
        summary = enrich_inventory.index_inventory(settings.source_path, state)
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
    from data_pipeline.enrich.application import download as downloader
    from data_pipeline.enrich.application import session

    settings = context.build_settings(source, work_dir)
    with session.open_work_state(settings, rebind=rebind_source) as state:
        if retry_failed:
            print(f"requeued {state.reset_failed_downloads()} failed downloads")
        summary = asyncio.run(downloader.run_downloads(state, settings))
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
    from data_pipeline.enrich.application import extract as pipeline
    from data_pipeline.enrich.application import session
    from data_pipeline.shared.infrastructure.settings import load_api_keys

    settings = context.build_settings(source, work_dir)
    api_keys = load_api_keys(context.current_paths().env_file)
    with session.open_work_state(settings, rebind=rebind_source) as state:
        if reparse_cloud:
            print(f"requeued {state.requeue_cloud_parses()} documents for LlamaParse")
        summary = pipeline.extract_documents(state, settings, api_keys=api_keys)
    return _summary(summary, json_output)


@app.command()
def run(
    source: SourceOption = None,
    work_dir: WorkDirOption = None,
    json_output: JsonOption = False,
    rebind_source: RebindOption = False,
) -> int:
    """Run inventory (if needed), download and extract in one go."""
    from data_pipeline.enrich.application import download as downloader
    from data_pipeline.enrich.application import extract as pipeline
    from data_pipeline.enrich.application import inventory as enrich_inventory
    from data_pipeline.enrich.application import session
    from data_pipeline.shared.infrastructure.settings import load_api_keys

    settings = context.build_settings(source, work_dir)
    api_keys = load_api_keys(context.current_paths().env_file)
    with session.open_work_state(settings, rebind=rebind_source) as state:
        if not state.inventory_done(enrich_inventory.source_key(settings.source_path)):
            enrich_inventory.index_inventory(settings.source_path, state)
        asyncio.run(downloader.run_downloads(state, settings))
        summary = pipeline.extract_documents(state, settings, api_keys=api_keys)
    return _summary(summary, json_output)


@app.command()
def status(
    source: SourceOption = None,
    work_dir: WorkDirOption = None,
    json_output: JsonOption = False,
    rebind_source: RebindOption = False,
) -> int:
    """Report downloaded, parsed, failed, pending and quota-blocked work."""
    from data_pipeline.enrich.application import session

    settings = context.build_settings(source, work_dir)
    with session.open_work_state(settings, rebind=rebind_source) as state:
        summary = state.summary()
    return _summary(summary, json_output)


@app.command()
def export(
    output: Annotated[Path, typer.Option(help="Directory to write the enrichment export to.")],
    source: SourceOption = None,
    work_dir: WorkDirOption = None,
    rebind_source: RebindOption = False,
) -> int:
    """Export the extracted documents of the work directory."""
    from data_pipeline.enrich.application import export as enrich_export
    from data_pipeline.enrich.application import session

    settings = context.build_settings(source, work_dir)
    with session.open_work_state(settings, rebind=rebind_source) as state:
        enrich_export.export_enrichment(state, output)
    return 0


@app.command("reconcile-job")
def reconcile_job(
    request_id: Annotated[str, typer.Option(help="Cloud request id.")],
    job_id: Annotated[str, typer.Option(help="Cloud job id.")],
    work_dir: WorkDirOption = None,
) -> int:
    """Show the recorded status of a cloud parse job."""
    from data_pipeline.enrich.infrastructure.sqlite import work_state as enrich_state

    settings = context.build_settings(None, work_dir)
    status_value = enrich_state.read_cloud_job_status(settings.work_path, request_id, job_id)
    print(
        json.dumps({"request_id": request_id, "job_id": job_id, "status": status_value}, indent=2)
    )
    return 0
