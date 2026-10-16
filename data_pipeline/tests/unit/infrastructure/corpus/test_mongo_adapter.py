from __future__ import annotations

from typing import Any

from data_pipeline.infrastructure.corpus.mongo import MAX_BATCH_OPERATIONS, MongoCorpusDatabase


class FakeCollection:
    """Records each bulk write and the documents it upserted."""

    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}
        self.batches: list[int] = []

    def bulk_write(self, operations: list[Any], *, ordered: bool) -> None:
        self.batches.append(len(operations))
        for operation in operations:
            doc = operation._doc
            self.docs[doc["_id"]] = doc


class FakeDatabase(dict[str, FakeCollection]):
    def __missing__(self, name: str) -> FakeCollection:
        self[name] = FakeCollection()
        return self[name]


def test_replace_many_writes_in_batches_of_at_most_100() -> None:
    db = FakeDatabase()
    adapter = MongoCorpusDatabase(db)  # type: ignore[arg-type]
    docs = [{"_id": str(n), "snapshot_id": "s"} for n in range(250)]

    written = adapter.replace_many("links", docs)

    assert written == 250
    assert db["links"].batches == [MAX_BATCH_OPERATIONS, MAX_BATCH_OPERATIONS, 50]
    assert set(db["links"].docs) == {str(n) for n in range(250)}
