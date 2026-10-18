from __future__ import annotations

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
