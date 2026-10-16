from __future__ import annotations

import json
from pathlib import Path

import pytest

from data_pipeline.enrich.inventory import index_inventory, source_key
from data_pipeline.enrich.models import SourceProgress
from data_pipeline.enrich.session import open_work_state
from data_pipeline.enrich.state import WorkState, read_crawl_id
from data_pipeline.shared.domain.config import Settings


def write_source(
    folder: Path, rows: list[dict[str, object]], *, crawl_id: str | None = "c1"
) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "articles.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    if crawl_id is not None:
        (folder / "README.json").write_text(json.dumps({"crawl_id": crawl_id}), encoding="utf-8")
    return folder


def page(url: str, *links: str) -> dict[str, object]:
    return {"url": url, "links": [{"url": link, "text": link} for link in links], "assets": []}


def refs(state: WorkState) -> set[tuple[str, str]]:
    return {(r[0], r[1]) for r in state.conn.execute("SELECT source_url, target_url FROM refs")}


def test_read_crawl_id_from_readme_or_summary(tmp_path: Path) -> None:
    final = write_source(tmp_path / "final", [], crawl_id="c1")
    changes = write_source(tmp_path / "changes", [], crawl_id=None)
    (changes / "summary.json").write_text(json.dumps({"crawl_id": "c1"}), encoding="utf-8")

    assert read_crawl_id(final) == "c1"
    assert read_crawl_id(changes) == "c1"
    assert read_crawl_id(write_source(tmp_path / "plain", [], crawl_id=None)) is None


def test_open_rejects_other_crawl_id(tmp_path: Path) -> None:
    with WorkState.open(tmp_path / "work", "c1"):
        pass

    with WorkState.open(tmp_path / "work", "c1"):
        pass
    with (
        pytest.raises(ValueError, match="--rebind-source"),
        WorkState.open(tmp_path / "work", "c2"),
    ):
        pass


def test_rebind_switches_crawl_id(tmp_path: Path) -> None:
    with WorkState.open(tmp_path / "work", "c1"):
        pass

    with WorkState.open(tmp_path / "work", "c2", rebind=True):
        pass
    with WorkState.open(tmp_path / "work", "c2"):
        pass


def test_source_without_crawl_id_is_rejected(tmp_path: Path) -> None:
    source = write_source(tmp_path / "final", [], crawl_id=None)
    settings = Settings(project_root=tmp_path, work_dir=tmp_path / "work", source=source)

    with (
        pytest.raises(ValueError, match="crawl_id"),
        open_work_state(settings, rebind=False),
    ):
        pass


def test_inventory_keeps_a_cursor_per_source(tmp_path: Path) -> None:
    final = write_source(tmp_path / "final", [page("https://a.test/", "https://a.test/f.pdf")])
    changes = write_source(
        tmp_path / "changes" / "R", [page("https://a.test/new", "https://a.test/g.pdf")]
    )
    with WorkState.open(tmp_path / "work", "c1") as state:
        index_inventory(final, state)
        index_inventory(changes, state)
        index_inventory(final, state)

        assert state.inventory_done(source_key(final))
        assert state.inventory_done(source_key(changes))
        urls = {row[0] for row in state.conn.execute("SELECT url FROM inventory")}
        assert urls == {"https://a.test/f.pdf", "https://a.test/g.pdf"}


def test_modified_page_refs_are_replaced(tmp_path: Path) -> None:
    final = write_source(tmp_path / "final", [page("https://a.test/hub", "https://a.test/old.pdf")])
    changes = write_source(
        tmp_path / "changes" / "R", [page("https://a.test/hub", "https://a.test/new.pdf")]
    )
    (changes / "changes.jsonl").write_text(
        json.dumps({"url": "https://a.test/hub", "change": "modified"}) + "\n", encoding="utf-8"
    )
    with WorkState.open(tmp_path / "work", "c1") as state:
        index_inventory(final, state)
        index_inventory(changes, state)

        assert refs(state) == {("https://a.test/hub", "https://a.test/new.pdf")}


def test_resumed_changes_inventory_does_not_delete_refs_again(tmp_path: Path) -> None:
    changes = write_source(
        tmp_path / "changes" / "R",
        [page("https://a.test/hub", "https://a.test/new.pdf")],
    )
    (changes / "changes.jsonl").write_text(
        json.dumps({"url": "https://a.test/hub", "change": "modified"}) + "\n", encoding="utf-8"
    )
    with WorkState.open(tmp_path / "work", "c1") as state:
        stat = (changes / "articles.jsonl").stat()
        progress = SourceProgress(source_key(changes), stat.st_size, stat.st_mtime_ns, stat.st_size)
        state.add_inventory_batch([], [], progress)
        state.conn.execute(
            "INSERT INTO refs(source_url,target_url,relation) VALUES(?,?,?)",
            ("https://a.test/hub", "https://a.test/kept", "hyperlink"),
        )
        state.conn.commit()

        index_inventory(changes, state)

        assert ("https://a.test/hub", "https://a.test/kept") in refs(state)


def test_inventory_restarts_when_source_file_changes(tmp_path: Path) -> None:
    final = write_source(tmp_path / "final", [page("https://a.test/", "https://a.test/f.pdf")])
    with WorkState.open(tmp_path / "work", "c1") as state:
        index_inventory(final, state)
        write_source(
            final,
            [page("https://a.test/rewritten-hub-with-a-longer-url", "https://a.test/g.pdf")],
        )

        index_inventory(final, state)

        urls = {row[0] for row in state.conn.execute("SELECT url FROM inventory")}
        assert urls == {"https://a.test/f.pdf", "https://a.test/g.pdf"}


def test_inventory_returns_early_when_done(tmp_path: Path) -> None:
    final = write_source(tmp_path / "final", [page("https://a.test/", "https://a.test/f.pdf")])
    with WorkState.open(tmp_path / "work", "c1") as state:
        index_inventory(final, state)
        state.conn.execute("DELETE FROM inventory")
        state.conn.commit()

        summary = index_inventory(final, state)

        assert summary.candidates == 0
        assert state.conn.execute("SELECT count(*) FROM inventory").fetchone()[0] == 0


def test_refs_stored_only_for_document_targets(tmp_path: Path) -> None:
    row = page("https://a.test/", "https://a.test/f.pdf", "https://a.test/other-page")
    row["assets"] = [{"url": "https://a.test/logo.png", "role": "inline_image"}]
    final = write_source(tmp_path / "final", [row])
    with WorkState.open(tmp_path / "work", "c1") as state:
        index_inventory(final, state)

        assert refs(state) == {("https://a.test/", "https://a.test/f.pdf")}


def test_inventory_is_not_done_after_source_rewrite(tmp_path: Path) -> None:
    final = write_source(tmp_path / "final", [page("https://a.test/", "https://a.test/f.pdf")])
    with WorkState.open(tmp_path / "work", "c1") as state:
        index_inventory(final, state)
        assert state.inventory_done(source_key(final))

        write_source(final, [page("https://a.test/refreshed-page", "https://a.test/g.pdf")])

        assert not state.inventory_done(source_key(final))
