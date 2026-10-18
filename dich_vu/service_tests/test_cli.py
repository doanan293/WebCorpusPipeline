from __future__ import annotations

from pathlib import Path

import pytest
from aiohttp import web

from data_pipeline.domain.shared.config import ProjectPaths
from dich_vu.api import common
from dich_vu.cli import main


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


def test_serve_fetcher_uses_port_option(
    project: ProjectPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    served: list[tuple[web.Application, int]] = []

    def fake_run(app: web.Application, port: int, background: object = ()) -> None:
        served.append((app, port))

    monkeypatch.setattr(common, "run", fake_run)
    config = tmp_path / "fetcher.yaml"
    config.write_text("port: 8800\n", encoding="utf-8")
    assert main(["serve", "fetcher", "--config", str(config), "--port", "8801"]) == 0
    ((app, port),) = served
    assert port == 8801
    paths = sorted(
        (route.method, route.resource.canonical) for route in app.router.routes() if route.resource
    )
    assert ("GET", "/ping") in paths
    assert ("POST", "/load") in paths


def test_serve_parser_runs_parse(
    project: ProjectPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    served: list[tuple[web.Application, int]] = []

    def fake_run(app: web.Application, port: int, background: object = ()) -> None:
        served.append((app, port))

    monkeypatch.setattr(common, "run", fake_run)
    config = tmp_path / "parser.yaml"
    config.write_text(f"port: 6600\ncache_dir: {tmp_path / 'cache'}\n", encoding="utf-8")
    assert main(["serve", "parser", "--config", str(config)]) == 0
    ((app, port),) = served
    assert port == 6600
    assert app._client_max_size > 100 * 1024 * 1024
    assert [
        route.method
        for route in app.router.routes()
        if route.resource and route.resource.canonical == "/parse"
    ] == ["POST"]


def test_serve_storage_runs_add_on_2200(
    project: ProjectPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    served: list[tuple[web.Application, int]] = []

    def fake_run(app: web.Application, port: int, background: object = ()) -> None:
        served.append((app, port))

    monkeypatch.setattr(common, "run", fake_run)
    monkeypatch.setenv("MONGODB_URI", "mongodb://127.0.0.1:1")
    assert main(["serve", "storage"]) == 0
    ((app, port),) = served
    assert port == 2200
    assert [
        route.method
        for route in app.router.routes()
        if route.resource and route.resource.canonical == "/add"
    ] == ["POST"]


def test_serve_aggregator_runs_add_on_3300(
    project: ProjectPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    served: list[tuple[web.Application, int]] = []

    def fake_run(app: web.Application, port: int, background: object = ()) -> None:
        served.append((app, port))

    monkeypatch.setattr(common, "run", fake_run)
    hosts = tmp_path / "active.txt"
    hosts.write_text("hust.edu.vn\n", encoding="utf-8")
    config = tmp_path / "aggregator.yaml"
    config.write_text(
        f"hosts_file: {hosts}\ndatabase: {tmp_path / 'a.sqlite3'}\noutbox: {tmp_path / 'o.jsonl'}\n",
        encoding="utf-8",
    )
    assert main(["serve", "aggregator", "--config", str(config)]) == 0
    ((_app, port),) = served
    assert port == 3300


@pytest.mark.parametrize(
    "args",
    [
        ["--help"],
        ["serve", "--help"],
        ["serve", "controller", "--help"],
        ["serve", "fetcher", "--help"],
        ["serve", "parser", "--help"],
        ["serve", "aggregator", "--help"],
        ["serve", "storage", "--help"],
    ],
)
def test_help_for_every_command(args: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    assert main(args) == 0
    assert "Usage" in capsys.readouterr().out
