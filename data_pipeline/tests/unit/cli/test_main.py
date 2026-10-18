from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from pymongo.errors import ServerSelectionTimeoutError

from data_pipeline import bootstrap
from data_pipeline.application.corpus import package
from data_pipeline.cli.main import main


@pytest.mark.parametrize(
    "args",
    [
        ["--help"],
        ["reextract", "--help"],
        ["corpus", "--help"],
        ["corpus", "verify", "--help"],
        ["domains", "audit", "--help"],
        ["eval", "extraction", "--help"],
    ],
)
def test_help_for_every_command(args: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    assert main(args) == 0
    assert "Usage" in capsys.readouterr().out


def test_business_errors_print_an_error_line_and_exit_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:

    def bad(corpus: Path, files: object) -> dict[str, object]:
        msg = "bad corpus"
        raise ValueError(msg)

    monkeypatch.setattr(package, "verify_corpus", bad)

    assert main(["corpus", "verify", "--corpus", "x"]) == 2
    assert capsys.readouterr().err.strip() == "Error: bad corpus"


def test_mongo_error_is_one_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def unreachable(*args: object, **kwargs: object) -> object:
        msg = "localhost:27017: [Errno 111] Connection refused"
        raise ServerSelectionTimeoutError(msg)

    monkeypatch.setattr(bootstrap, "connect", unreachable)

    assert main(["corpus", "query", "--url", "https://h/"]) == 1
    err = capsys.readouterr().err.strip()
    assert (
        err == "Error: ServerSelectionTimeoutError: localhost:27017: [Errno 111] Connection refused"
    )


def test_called_process_error_shows_stderr(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def failing(*args: object, **kwargs: object) -> object:
        raise subprocess.CalledProcessError(2, ["soffice"], "", "source file could not be loaded")

    monkeypatch.setattr(bootstrap, "connect", failing)

    assert main(["corpus", "query", "--url", "https://h/"]) == 1
    assert "source file could not be loaded" in capsys.readouterr().err


def test_keyboard_interrupt_exits_130(monkeypatch: pytest.MonkeyPatch) -> None:
    def interrupted(*_: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(package, "verify_corpus", interrupted)

    assert main(["corpus", "verify", "--corpus", "x"]) == 130
