from __future__ import annotations

from pathlib import Path

import pytest

from crawler import bootstrap
from crawler.domain.shared.config import Settings


def test_mongo_database_closes_client_on_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    closed: list[bool] = []

    class FakeClient:
        def __getitem__(self, name: str) -> object:
            return object()

        def close(self) -> None:
            closed.append(True)

    def connect(settings: Settings) -> FakeClient:
        return FakeClient()

    monkeypatch.setattr(bootstrap, "connect", connect)

    with pytest.raises(RuntimeError), bootstrap.mongo_database(Settings(project_root=tmp_path)):
        msg = "boom"
        raise RuntimeError(msg)

    assert closed == [True]
