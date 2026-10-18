from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from data_pipeline.infrastructure.aggregator.outbox import JsonlOutbox


def test_outbox_survives_reopen(tmp_path: Path) -> None:
    path = tmp_path / "outbox.jsonl"
    outbox = JsonlOutbox(path)
    outbox.append("storage", {"url": "u1"})
    outbox.append("controller", {"urls": ["u2"]})
    reopened = JsonlOutbox(path)
    entries = list(reopened.pending())
    assert [(entry.target, entry.payload) for entry in entries] == [
        ("storage", {"url": "u1"}),
        ("controller", {"urls": ["u2"]}),
    ]
    reopened.remove([entries[0].id])
    assert [entry.payload for entry in JsonlOutbox(path).pending()] == [{"urls": ["u2"]}]
    reopened.append("storage", {"url": "u3"})
    assert len({entry.id for entry in JsonlOutbox(path).pending()}) == 2


def test_empty_outbox(tmp_path: Path) -> None:
    assert list(JsonlOutbox(tmp_path / "none.jsonl").pending()) == []


def test_corrupt_outbox_line_is_skipped(tmp_path: Path) -> None:
    path = tmp_path / "outbox.jsonl"
    outbox = JsonlOutbox(path)
    outbox.append("storage", {"url": "u1"})
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"id": 2, "target": "stor')  # torn by a kill mid-write
    outbox = JsonlOutbox(path)
    assert [entry.payload for entry in outbox.pending()] == [{"url": "u1"}]
    outbox.append("controller", {"urls": ["u2"]})
    assert [entry.payload for entry in JsonlOutbox(path).pending()] == [
        {"url": "u1"},
        {"urls": ["u2"]},
    ]


def test_dead_letters_are_kept_apart(tmp_path: Path) -> None:
    path = tmp_path / "outbox.jsonl"
    outbox = JsonlOutbox(path)
    outbox.dead("storage", {"url": "big"}, "rejected")
    assert list(outbox.pending()) == []
    assert '"big"' in (tmp_path / "outbox.dead.jsonl").read_text(encoding="utf-8")


def test_outbox_streams_instead_of_loading_everything(tmp_path: Path) -> None:
    path = tmp_path / "outbox.jsonl"
    outbox = JsonlOutbox(path)
    for index in range(1000):
        outbox.append("storage", {"url": f"u{index}", "content": "x" * 100})
    entries = outbox.pending()
    assert isinstance(entries, Iterator)  # one line at a time, not the whole file in memory
    first = next(entries)
    assert first.payload["url"] == "u0"
    outbox.remove([first.id, *range(3, 1001)])
    assert [entry.payload["url"] for entry in JsonlOutbox(path).pending()] == ["u1"]
