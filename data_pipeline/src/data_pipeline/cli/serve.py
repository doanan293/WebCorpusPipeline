from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

import typer

from data_pipeline import bootstrap
from data_pipeline.cli import context

app = typer.Typer(help="Run one block of the slide as an HTTP service.", no_args_is_help=True)


def _config_path(value: Path | None, name: str) -> Path:
    return context.pick(value, context.current_paths().root / "config" / name)


@app.command()
def controller(
    config: Annotated[
        Path | None, typer.Option(help="Controller settings (default: config/controller.yaml).")
    ] = None,
) -> int:
    """Điều khiển: POST /add on port 5200, sending queued URLs to the download nodes."""
    from data_pipeline.api import common
    from data_pipeline.api import controller as service

    settings = bootstrap.controller_config(_config_path(config, "controller.yaml"))
    app_ = service.create_service(
        bootstrap.url_queue(settings.database),
        bootstrap.downloader(settings),
        bootstrap.system_clock(),
        settings,
    )
    common.run(app_, settings.port)
    return 0


@app.command()
def fetcher(
    config: Annotated[
        Path | None, typer.Option(help="Fetcher settings (default: config/fetcher.yaml).")
    ] = None,
    port: Annotated[
        int | None, typer.Option(help="Port to listen on (default: from config).")
    ] = None,
) -> int:
    """Tải về: POST /load and GET /ping; sends downloads to PARSER_URL/parse."""
    from data_pipeline.api import common
    from data_pipeline.api import fetcher as service

    settings = bootstrap.fetcher_config(_config_path(config, "fetcher.yaml"))
    getter = bootstrap.page_getter(settings)
    parser = bootstrap.parser_client(os.environ.get("PARSER_URL", "http://parser:6600"))
    app_ = service.create_service(
        getter, bootstrap.renderer(settings), parser, closers=(getter.close, parser.close)
    )
    common.run(app_, port if port is not None else settings.port)
    return 0


@app.command()
def parser(
    config: Annotated[
        Path | None, typer.Option(help="Parser settings (default: config/parser.yaml).")
    ] = None,
) -> int:
    """Đọc nội dung: POST /parse; sends records to AGGREGATOR_URL/add."""
    from data_pipeline.api import common
    from data_pipeline.api import parser as service

    paths = context.current_paths()
    settings = bootstrap.parser_config(_config_path(config, "parser.yaml"))
    aggregator = bootstrap.aggregator_client(
        os.environ.get("AGGREGATOR_URL", "http://aggregator:3300")
    )
    readers = bootstrap.document_readers(settings, paths)
    app_ = service.create_service(readers, aggregator, closers=(aggregator.close,))
    common.run(app_, settings.port)
    return 0


@app.command()
def storage(
    port: Annotated[int, typer.Option(help="Port to listen on.")] = 2200,
) -> int:
    """Lưu trữ: POST /add into Mongo (MONGODB_URI, MONGODB_DATABASE)."""
    from data_pipeline.api import common
    from data_pipeline.api import storage as service

    defaults = bootstrap.load_settings(context.current_paths())
    store = bootstrap.corpus_store(
        os.environ.get("MONGODB_URI", defaults.mongo_uri),
        os.environ.get("MONGODB_DATABASE", defaults.mongo_database),
    )
    common.run(service.create_service(store, bootstrap.utc_now_iso), port)
    return 0


@app.command()
def aggregator(
    config: Annotated[
        Path | None, typer.Option(help="Aggregator settings (default: config/aggregator.yaml).")
    ] = None,
) -> int:
    """Tổng hợp dữ liệu: POST /add; records to STORAGE_URL, new URLs to CONTROLLER_URL."""
    from data_pipeline.api import aggregator as service
    from data_pipeline.api import common
    from data_pipeline.application.aggregator.seeds import seed_payload

    paths = context.current_paths()
    settings = bootstrap.aggregator_config(_config_path(config, "aggregator.yaml"))
    hosts_file = settings.hosts_file
    hosts = sorted(
        bootstrap.hostnames(hosts_file if hosts_file.is_absolute() else paths.root / hosts_file)
    )
    client = bootstrap.service_client(
        os.environ.get("STORAGE_URL", "http://storage:2200"),
        os.environ.get("CONTROLLER_URL", "http://controller:5200"),
    )
    app_ = service.create_service(
        settings,
        bootstrap.aggregator_state(settings.database),
        bootstrap.outbox(settings.outbox),
        client,
        seed_urls=seed_payload(hosts)["urls"],
        allowed_hosts=frozenset(hosts),
        closers=(client.close,),
    )
    common.run(app_, settings.port)
    return 0
