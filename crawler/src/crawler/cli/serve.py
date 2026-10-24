"""``crawler serve <block>``: run one block of the slide as an HTTP service from ``api/``."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

import typer

from crawler import bootstrap
from crawler.cli import context

app = typer.Typer(help="Run one block of the slide as an HTTP service.", no_args_is_help=True)


def _under_root(path: Path) -> Path:
    """Relative paths in config/*.yaml are relative to the project root, wherever this runs from."""
    return path if path.is_absolute() else context.current_paths().root / path


def _config_path(value: Path | None, name: str) -> Path:
    return value if value is not None else context.current_paths().root / "config" / name


@app.command()
def mediator(
    config: Annotated[
        Path | None, typer.Option(help="Mediator settings (default: config/mediator.yaml).")
    ] = None,
) -> int:
    """mediator: POST /add on port 5200, sending queued URLs to the download nodes."""
    from crawler.api import common
    from crawler.api import mediator as service

    settings = bootstrap.mediator_config(_config_path(config, "mediator.yaml"))
    database = _under_root(settings.database)
    with bootstrap.state_lease(database, "crawler serve mediator"):
        app_ = service.create_service(
            bootstrap.url_queue(database),
            bootstrap.downloader(settings),
            bootstrap.system_clock(),
            settings,
        )
        common.run(app_, settings.port)
    return 0


@app.command()
def downloader(
    config: Annotated[
        Path | None, typer.Option(help="Downloader settings (default: config/downloader.yaml).")
    ] = None,
    port: Annotated[
        int | None, typer.Option(help="Port to listen on (default: from config).")
    ] = None,
) -> int:
    """downloader: POST /load and GET /ping; sends downloads to READER_URL/parse."""
    from crawler.api import common
    from crawler.api import downloader as service

    settings = bootstrap.downloader_config(_config_path(config, "downloader.yaml"))
    getter = bootstrap.page_getter(settings)
    reader = bootstrap.reader_client(os.environ.get("READER_URL", "http://reader:6600"))
    app_ = service.create_service(
        getter, bootstrap.renderer(settings), reader, closers=(getter.close, reader.close)
    )
    common.run(app_, port if port is not None else settings.port)
    return 0


@app.command()
def reader(
    config: Annotated[
        Path | None, typer.Option(help="Reader settings (default: config/reader.yaml).")
    ] = None,
) -> int:
    """reader: POST /parse; sends records to MERGER_URL/add."""
    from crawler.api import common
    from crawler.api import reader as service

    paths = context.current_paths()
    settings = bootstrap.reader_config(_config_path(config, "reader.yaml"))
    merger = bootstrap.merger_client(os.environ.get("MERGER_URL", "http://merger:3300"))
    readers = bootstrap.document_readers(settings, paths)
    app_ = service.create_service(readers, merger, closers=(merger.close,))
    common.run(app_, settings.port)
    return 0


@app.command()
def storage(
    port: Annotated[int, typer.Option(help="Port to listen on.")] = 2200,
) -> int:
    """storage: POST /add into Mongo (MONGODB_URI, MONGODB_DATABASE)."""
    from crawler.api import common
    from crawler.api import storage as service

    defaults = bootstrap.load_settings(context.current_paths())
    store = bootstrap.corpus_store(
        os.environ.get("MONGODB_URI", defaults.mongo_uri),
        os.environ.get("MONGODB_DATABASE", defaults.mongo_database),
    )
    common.run(service.create_service(store, bootstrap.utc_now_iso), port)
    return 0


@app.command()
def merger(
    config: Annotated[
        Path | None, typer.Option(help="Merger settings (default: config/merger.yaml).")
    ] = None,
) -> int:
    """merger: POST /add; records to STORAGE_URL, new URLs to MEDIATOR_URL."""
    from crawler.api import common
    from crawler.api import merger as service

    settings = bootstrap.merger_config(_config_path(config, "merger.yaml"))
    seeds = bootstrap.seeds(_under_root(settings.seeds_file))
    initial_file = _under_root(settings.initial_scope_file)
    initial = bootstrap.hostnames(initial_file) if initial_file.exists() else frozenset()
    database, outbox = _under_root(settings.database), _under_root(settings.outbox)
    with bootstrap.state_lease(database, "crawler serve merger"):
        client = bootstrap.service_client(
            os.environ.get("STORAGE_URL", "http://storage:2200"),
            os.environ.get("MEDIATOR_URL", "http://mediator:5200"),
        )
        app_ = service.create_service(
            settings,
            bootstrap.merger_state(database),
            bootstrap.outbox(outbox),
            client,
            seeds=seeds,
            initial_scope=initial,
            closers=(client.close,),
        )
        common.run(app_, settings.port)
    return 0
