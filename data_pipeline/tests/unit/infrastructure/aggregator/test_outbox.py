from __future__ import annotations

from pathlib import Path

from data_pipeline.infrastructure.aggregator.outbox import JsonlOutbox


def test_outbox_survives_reopen(tmp_path: Path) -> None:
    path = tmp_path / "outbox.jsonl"
    outbox = JsonlOutbox(path)
    outbox.append("storage", {"url": "u1"})
    outbox.append("controller", {"urls": ["u2"]})
    reopened = JsonlOutbox(path)
    entries = reopened.pending()
    assert [(entry.target, entry.payload) for entry in entries] == [
        ("storage", {"url": "u1"}),
        ("controller", {"urls": ["u2"]}),
    ]
    reopened.remove([entries[0].id])
    assert [entry.payload for entry in JsonlOutbox(path).pending()] == [{"urls": ["u2"]}]
    reopened.append("storage", {"url": "u3"})
    assert len({entry.id for entry in JsonlOutbox(path).pending()}) == 2


def test_empty_outbox(tmp_path: Path) -> None:
    assert JsonlOutbox(tmp_path / "none.jsonl").pending() == []
