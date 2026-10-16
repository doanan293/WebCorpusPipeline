from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, Literal, cast

from data_pipeline.application.crawl.ports import CrawlUnitOfWork
from data_pipeline.domain.crawl.models import ReusedAssetTarget
from data_pipeline.infrastructure.crawl.sqlite import reuse
from data_pipeline.infrastructure.crawl.sqlite.refresh_store import latest_refresh_run
from data_pipeline.infrastructure.crawl.sqlite.unit_of_work import SqliteCrawlUnitOfWork


class SqliteCrawlStorage:
    """The ``CrawlStorage`` port: crawl states as ``state/index.sqlite3`` + ``manifest.json``."""

    def open(
        self,
        root: Path,
        *,
        phase: Literal["discover", "crawl"],
        input_path: Path,
        semantic_config: Mapping[str, object],
        runtime_config: Mapping[str, object],
        resume: bool = False,
        html_dir: Path | None = None,
    ) -> SqliteCrawlUnitOfWork:
        return SqliteCrawlUnitOfWork.open(
            root,
            phase=phase,
            input_path=input_path,
            semantic_config=semantic_config,
            runtime_config=runtime_config,
            resume=resume,
            html_dir=html_dir,
        )

    def open_existing(self, root: Path, *, html_dir: Path | None = None) -> SqliteCrawlUnitOfWork:
        return SqliteCrawlUnitOfWork.open_existing(root, html_dir=html_dir)

    def manifest(self, state_dir: Path) -> dict[str, Any] | None:
        path = state_dir / "manifest.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def latest_refresh_run(self, state_dir: Path) -> dict[str, object] | None:
        db_path = state_dir / "index.sqlite3"
        if not db_path.exists():
            return None
        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            return latest_refresh_run(connection)
        finally:
            connection.close()

    def validate_reuse_source(self, source_output: Path, input_path: Path) -> None:
        reuse.validate_reuse_source(source_output, input_path)

    def import_reused_content(
        self, source_output: Path, uow: CrawlUnitOfWork, input_path: Path
    ) -> None:
        """Copy a finished crawl's state into ``uow`` and list its assets to fetch again."""
        destination = cast(SqliteCrawlUnitOfWork, uow)
        reuse.import_reused_content(source_output, destination, input_path)
        reuse.prepare_reused_asset_targets(destination)

    def reused_asset_targets(self, uow: CrawlUnitOfWork) -> Iterator[ReusedAssetTarget]:
        return reuse.reused_asset_targets(cast(SqliteCrawlUnitOfWork, uow))
