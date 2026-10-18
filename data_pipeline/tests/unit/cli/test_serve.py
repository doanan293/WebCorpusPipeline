from __future__ import annotations

from pathlib import Path

import pytest
from aiohttp import web

from data_pipeline.api import common
from data_pipeline.cli.main import main
from data_pipeline.domain.shared.config import ProjectPaths


def test_serve_controller_runs_add_on_configured_port(
    project: ProjectPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    served: list[tuple[web.Application, int]] = []

    def fake_run(app: web.Application, port: int, background: object = ()) -> None:
        served.append((app, port))

    monkeypatch.setattr(common, "run", fake_run)
    config = tmp_path / "controller.yaml"
    config.write_text(
        f"port: 5201\ndownloaders: [http://n:8800]\ndatabase: {tmp_path / 'q.sqlite3'}\n",
        encoding="utf-8",
    )
    assert main(["serve", "controller", "--config", str(config)]) == 0
    ((app, port),) = served
    assert port == 5201
    assert [
        route.method
        for route in app.router.routes()
        if route.resource and route.resource.canonical == "/add"
    ] == ["POST"]


def test_serve_controller_bad_config_exits_2(project: ProjectPaths, tmp_path: Path) -> None:
    config = tmp_path / "controller.yaml"
    config.write_text("port: 5200\n", encoding="utf-8")
    assert main(["serve", "controller", "--config", str(config)]) == 2
