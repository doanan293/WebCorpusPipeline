from __future__ import annotations

import csv
import io
import subprocess
from pathlib import Path

from crawler.domain.shared.errors import ExternalFailure


class KaggleError(ExternalFailure):
    """A kaggle CLI call failed; the message carries its stderr."""


def _run(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(  # noqa: S603 -- fixed argv, no shell
            args, cwd=cwd, check=True, capture_output=True, text=True, env=None
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        msg = f"{' '.join(args[:3])} failed: {detail}"
        raise KaggleError(msg) from exc


class KaggleCli:
    """The ``DatasetPublisher`` port over the ``kaggle`` command line tool."""

    def create(self, folder: Path) -> None:
        _run(["kaggle", "datasets", "create", "-p", str(folder), "-u", "-t", "-r", "skip"])

    def version(self, folder: Path, message: str) -> None:
        _run(
            [
                "kaggle",
                "datasets",
                "version",
                "-p",
                str(folder),
                "-m",
                message,
                "-t",
                "-r",
                "skip",
            ]
        )

    def download(self, dataset: str, folder: Path) -> None:
        _run(["kaggle", "datasets", "download", dataset, "-p", str(folder), "--unzip"])

    def list_files(self, dataset: str) -> list[str]:
        result = _run(["kaggle", "datasets", "files", dataset, "-v", "--page-size", "200"])
        lines = result.stdout.splitlines()
        try:
            header = next(
                index
                for index, line in enumerate(lines)
                if line.strip() == "name,size,creationDate"
            )
        except StopIteration as exc:
            msg = "Kaggle did not return a file listing"
            raise ValueError(msg) from exc
        rows = csv.DictReader(io.StringIO("\n".join(lines[header:])))
        names = [str(row["name"]).strip() for row in rows if row.get("name")]
        if not names:
            msg = "Kaggle dataset file listing is empty"
            raise ValueError(msg)
        return names

    def download_file(self, dataset: str, name: str, folder: Path) -> None:
        _run(
            [
                "kaggle",
                "datasets",
                "download",
                dataset,
                "-f",
                name,
                "-p",
                str(folder),
                "--unzip",
            ]
        )
