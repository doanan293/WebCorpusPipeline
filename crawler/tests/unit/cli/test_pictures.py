from __future__ import annotations

from typing import Any

import pytest

from crawler import bootstrap
from crawler.application.storage.ports import ImageResult
from crawler.cli.main import main
from crawler.domain.shared.config import ProjectPaths


class _Store:
    def __init__(self) -> None:
        self.saved: dict[str, dict[str, Any]] = {}

    def pending(self, *, retry_errors: bool) -> list[str]:
        return ["https://h/a.png"]

    def save_picture(self, url: str, **fields: Any) -> None:
        self.saved[url] = fields


class _Getter:
    async def get(self, url: str) -> ImageResult:
        return ImageResult(200, "image/png", b"\x89PNG")

    async def close(self) -> None:
        return None


def test_pictures_fetch_saves_images(
    project: ProjectPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _Store()

    def picture_store(uri: str, database: str) -> _Store:
        return store

    monkeypatch.setattr(bootstrap, "picture_store", picture_store)
    monkeypatch.setattr(bootstrap, "image_getter", _Getter)
    assert main(["pictures", "fetch", "--workers", "2"]) == 0
    assert store.saved["https://h/a.png"]["b64"] == "iVBORw=="
