from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from data_pipeline.shared.domain.config import ProjectPaths, Settings
from data_pipeline.shared.infrastructure.settings import load_settings, project_paths

SourceOption = Annotated[
    Path | None, typer.Option(help="Crawl output to read (default: data/crawl/final).")
]
WorkDirOption = Annotated[
    Path | None, typer.Option(help="Enrichment work directory (default: data/enrichment).")
]
JsonOption = Annotated[bool, typer.Option("--json", help="Print the summary as JSON.")]
RebindOption = Annotated[
    bool,
    typer.Option(
        "--rebind-source", help="move the work directory to the crawl the source belongs to"
    ),
]


def current_paths() -> ProjectPaths:
    """Return the project layout the commands operate on."""
    return project_paths()


def pick(value: Path | None, default: Path) -> Path:
    """Use a path typed by the user as given, otherwise the project default."""
    return value if value is not None else default


def build_settings(source: Path | None, work_dir: Path | None) -> Settings:
    """Load pipeline settings, applying ``--source`` / ``--work-dir`` overrides.

    Paths typed by the user are resolved against the working directory; the defaults
    stay inside the project no matter where the command runs.
    """
    overrides: dict[str, object] = {}
    if source is not None:
        overrides["source"] = source.resolve()
    if work_dir is not None:
        overrides["work_dir"] = work_dir.resolve()
    return load_settings(current_paths(), overrides)
