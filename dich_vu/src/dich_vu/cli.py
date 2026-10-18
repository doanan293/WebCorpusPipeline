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
    name="dich-vu",
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


def _config_path(value: Path | None, name: str) -> Path:
    return value if value is not None else current_paths().root / "config" / name


@serve.command()
def controller(
    config: Annotated[
        Path | None, typer.Option(help="Controller settings (default: config/controller.yaml).")
    ] = None,
) -> int:
    """Điều khiển: POST /add on port 5200, sending queued URLs to the download nodes."""
    from dich_vu.api import common
    from dich_vu.api import controller as service

    settings = bootstrap.controller_config(_config_path(config, "controller.yaml"))
    with bootstrap.state_lease(settings.database, "dich-vu controller"):
        app_ = service.create_service(
            bootstrap.url_queue(settings.database),
            bootstrap.downloader(settings),
            bootstrap.system_clock(),
            settings,
        )
        common.run(app_, settings.port)
    return 0


@serve.command()
def fetcher(
    config: Annotated[
        Path | None, typer.Option(help="Fetcher settings (default: config/fetcher.yaml).")
    ] = None,
    port: Annotated[
        int | None, typer.Option(help="Port to listen on (default: from config).")
    ] = None,
) -> int:
    """Tải về: POST /load and GET /ping; sends downloads to PARSER_URL/parse."""
    from dich_vu.api import common
    from dich_vu.api import fetcher as service

    settings = bootstrap.fetcher_config(_config_path(config, "fetcher.yaml"))
    getter = bootstrap.page_getter(settings)
    parser = bootstrap.parser_client(os.environ.get("PARSER_URL", "http://parser:6600"))
    app_ = service.create_service(
        getter, bootstrap.renderer(settings), parser, closers=(getter.close, parser.close)
    )
    common.run(app_, port if port is not None else settings.port)
    return 0


@serve.command()
def parser(
    config: Annotated[
        Path | None, typer.Option(help="Parser settings (default: config/parser.yaml).")
    ] = None,
) -> int:
    """Đọc nội dung: POST /parse; sends records to AGGREGATOR_URL/add."""
    from dich_vu.api import common
    from dich_vu.api import parser as service

    paths = current_paths()
    settings = bootstrap.parser_config(_config_path(config, "parser.yaml"))
    aggregator = bootstrap.aggregator_client(
        os.environ.get("AGGREGATOR_URL", "http://aggregator:3300")
    )
    readers = bootstrap.document_readers(settings, paths)
    app_ = service.create_service(readers, aggregator, closers=(aggregator.close,))
    common.run(app_, settings.port)
    return 0


@serve.command()
def storage(
    port: Annotated[int, typer.Option(help="Port to listen on.")] = 2200,
) -> int:
    """Lưu trữ: POST /add into Mongo (MONGODB_URI, MONGODB_DATABASE)."""
    from dich_vu.api import common
    from dich_vu.api import storage as service

    defaults = bootstrap.load_settings(current_paths())
    store = bootstrap.corpus_store(
        os.environ.get("MONGODB_URI", defaults.mongo_uri),
        os.environ.get("MONGODB_DATABASE", defaults.mongo_database),
    )
    common.run(service.create_service(store, bootstrap.utc_now_iso), port)
    return 0


@serve.command()
def aggregator(
    config: Annotated[
        Path | None, typer.Option(help="Aggregator settings (default: config/aggregator.yaml).")
    ] = None,
) -> int:
    """Tổng hợp dữ liệu: POST /add; records to STORAGE_URL, new URLs to CONTROLLER_URL."""
    from dich_vu.api import aggregator as service
    from dich_vu.api import common

    paths = current_paths()
    settings = bootstrap.aggregator_config(_config_path(config, "aggregator.yaml"))
    seeds = bootstrap.seeds(paths.root / settings.seeds_file)
    initial_file = paths.root / settings.initial_scope_file
    initial = bootstrap.hostnames(initial_file) if initial_file.exists() else frozenset()
    with bootstrap.state_lease(settings.database, "dich-vu aggregator"):
        client = bootstrap.service_client(
            os.environ.get("STORAGE_URL", "http://storage:2200"),
            os.environ.get("CONTROLLER_URL", "http://controller:5200"),
        )
        app_ = service.create_service(
            settings,
            bootstrap.aggregator_state(settings.database),
            bootstrap.outbox(settings.outbox),
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
            prog_name="dich-vu",
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
