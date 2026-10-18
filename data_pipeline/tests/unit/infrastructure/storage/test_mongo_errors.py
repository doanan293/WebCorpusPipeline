from __future__ import annotations

from typing import Any

import pytest
from pymongo.errors import DocumentTooLarge, WriteError

from data_pipeline.application.storage.ports import StoreRejectedError
from data_pipeline.infrastructure.storage.mongo import MongoCorpusStore


class _Collection:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def replace_one(self, *args: Any, **kwargs: Any) -> None:
        raise self.error


class _Database:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def __getitem__(self, name: str) -> _Collection:
        return _Collection(self.error)


@pytest.mark.parametrize("error", [DocumentTooLarge("17 MB"), WriteError("bad", code=2)])
def test_refused_writes_are_store_rejected(error: Exception) -> None:
    store = MongoCorpusStore(_Database(error))  # type: ignore[arg-type]
    with pytest.raises(StoreRejectedError):
        store.upsert("other", {"url": "https://h/big.pdf", "content": "x"})
