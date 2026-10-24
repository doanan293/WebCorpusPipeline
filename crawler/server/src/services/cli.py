from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, NoReturn

import typer

from data_pipeline import bootstrap
from data_pipeline.cli import errors
from data_pipeline.domain.shared.config import ProjectPaths

app = typer.Typer(
    name="services",
    help="The five blocks of the slide as HTTP services.",
    no_args_is_help=True,
    add_completion=False,
)
serve = typer.Typer(help="Run one block of the slide as an HTTP service.", no_args_is_help=True)
app.add_typer(serve, name="serve")


def current_paths() -> ProjectPaths:
    """The data_pipeline project the services read their config and data from."""
    return bootstrap.project_paths()


@app.callback()
def _load_project_env() -> None:
    bootstrap.load_env(current_paths())


def _under_root(path: Path) -> Path:
    """Relative paths in config/*.yaml are relative to data_pipeline/, wherever this runs from."""
    return path if path.is_absolute() else current_paths().root / path


def _config_path(value: Path | None, name: str) -> Path:
    return value if value is not None else current_paths().root / "config" / name


@serve.command()
def mediator(
    config: Annotated[
        Path | None, typer.Option(help="Mediator settings (default: config/mediator.yaml).")
    ] = None,
) -> int:
    """mediator: POST /add on port 5200, sending queued URLs to the download nodes."""
    from services.api import common
    from services.api import mediator as service

    settings = bootstrap.mediator_config(_config_path(config, "mediator.yaml"))
    database = _under_root(settings.database)
    with bootstrap.state_lease(database, "services mediator"):
        app_ = service.create_service(
            bootstrap.url_queue(database),
            bootstrap.downloader(settings),
            bootstrap.system_clock(),
            settings,
        )
        common.run(app_, settings.port)
    return 0


@serve.command()
def downloader(
    config: Annotated[
        Path | None, typer.Option(help="Downloader settings (default: config/downloader.yaml).")
    ] = None,
    port: Annotated[
        int | None, typer.Option(help="Port to listen on (default: from config).")
    ] = None,
) -> int:
    """downloader: POST /load and GET /ping; sends downloads to READER_URL/parse."""
    from services.api import common
    from services.api import downloader as service

    settings = bootstrap.downloader_config(_config_path(config, "downloader.yaml"))
    getter = bootstrap.page_getter(settings)
    reader = bootstrap.reader_client(os.environ.get("READER_URL", "http://reader:6600"))
    app_ = service.create_service(
        getter, bootstrap.renderer(settings), reader, closers=(getter.close, reader.close)
    )
    common.run(app_, port if port is not None else settings.port)
    return 0


@serve.command()
def reader(
    config: Annotated[
        Path | None, typer.Option(help="Reader settings (default: config/reader.yaml).")
    ] = None,
) -> int:
    """reader: POST /parse; sends records to MERGER_URL/add."""
    from services.api import common
    from services.api import reader as service

    paths = current_paths()
    settings = bootstrap.reader_config(_config_path(config, "reader.yaml"))
    merger = bootstrap.merger_client(os.environ.get("MERGER_URL", "http://merger:3300"))
    readers = bootstrap.document_readers(settings, paths)
    app_ = service.create_service(readers, merger, closers=(merger.close,))
    common.run(app_, settings.port)
    return 0


@serve.command()
def storage(
    port: Annotated[int, typer.Option(help="Port to listen on.")] = 2200,
) -> int:
    """storage: POST /add into Mongo (MONGODB_URI, MONGODB_DATABASE)."""
    from services.api import common
    from services.api import storage as service

    defaults = bootstrap.load_settings(current_paths())
    store = bootstrap.corpus_store(
        os.environ.get("MONGODB_URI", defaults.mongo_uri),
        os.environ.get("MONGODB_DATABASE", defaults.mongo_database),
    )
    common.run(service.create_service(store, bootstrap.utc_now_iso), port)
    return 0


@serve.command()
def merger(
    config: Annotated[
        Path | None, typer.Option(help="Merger settings (default: config/merger.yaml).")
    ] = None,
) -> int:
    """merger: POST /add; records to STORAGE_URL, new URLs to MEDIATOR_URL."""
    from services.api import common
    from services.api import merger as service

    settings = bootstrap.merger_config(_config_path(config, "merger.yaml"))
    seeds = bootstrap.seeds(_under_root(settings.seeds_file))
    initial_file = _under_root(settings.initial_scope_file)
    initial = bootstrap.hostnames(initial_file) if initial_file.exists() else frozenset()
    database, outbox = _under_root(settings.database), _under_root(settings.outbox)
    with bootstrap.state_lease(database, "services merger"):
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


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and translate failures into exit codes (2 usage/error, 130 interrupt)."""
    try:
        result = app(
            args=list(argv) if argv is not None else None,
            prog_name="services",
            standalone_mode=False,
        )
    except typer.TyperException as exc:
        show = getattr(exc, "show", None)
        if callable(show):
            show()
        else:
            errors.fail(str(exc))
        return exc.exit_code
    except (typer.Abort, KeyboardInterrupt):
        return 130
    except (FileNotFoundError, FileExistsError, ValueError, RuntimeError) as exc:
        return errors.fail(str(exc))
    except Exception as exc:
        message = errors.external_failure_message(exc)
        if message is None:
            raise
        return errors.fail(message, exit_code=1)
    return result if isinstance(result, int) else 0


def run() -> NoReturn:
    sys.exit(main())
