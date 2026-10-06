from __future__ import annotations

import json
from pathlib import Path

import pytest

from ir_data.inventory import index_inventory, source_key
from ir_data.state import WorkState, fingerprint_source, read_crawl_id


def write_source(folder: Path, rows: list[dict[str, object]], *, crawl_id: str | None = "c1") -> Path:
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


def test_work_dir_accepts_refreshed_source_of_same_crawl(tmp_path: Path) -> None:
    source = write_source(tmp_path / "final", [page("https://a.test/")])
    with WorkState.open(tmp_path / "work", fingerprint_source(source), crawl_id="c1"):
        pass
    write_source(source, [page("https://a.test/"), page("https://a.test/new")])

    with WorkState.open(tmp_path / "work", fingerprint_source(source), crawl_id="c1"):
        pass
    with pytest.raises(ValueError, match="--rebind-source"):
        with WorkState.open(tmp_path / "work", "other", crawl_id="c2"):
            pass


def test_rebind_adopts_legacy_work_dir(tmp_path: Path) -> None:
    with WorkState.open(tmp_path / "work", "old-fingerprint"):
        pass
    with pytest.raises(ValueError, match="source fingerprint changed"):
        with WorkState.open(tmp_path / "work", "new-fingerprint", crawl_id="c1"):
            pass

    with WorkState.open(tmp_path / "work", "new-fingerprint", crawl_id="c1", rebind=True):
        pass
    with WorkState.open(tmp_path / "work", "newer-fingerprint", crawl_id="c1"):
        pass


def test_inventory_keeps_a_cursor_per_source(tmp_path: Path) -> None:
    final = write_source(tmp_path / "final", [page("https://a.test/", "https://a.test/f.pdf")])
    changes = write_source(tmp_path / "changes" / "R", [page("https://a.test/new", "https://a.test/g.pdf")])
    with WorkState.open(tmp_path / "work", "fp", crawl_id="c1") as state:
        index_inventory(final, state)
        index_inventory(changes, state)
        index_inventory(final, state)

        assert state.inventory_done(source_key(final))
        assert state.inventory_done(source_key(changes))
        urls = {row[0] for row in state.conn.execute("SELECT url FROM inventory")}
        assert urls == {"https://a.test/f.pdf", "https://a.test/g.pdf"}


def test_modified_page_refs_are_replaced(tmp_path: Path) -> None:
    final = write_source(tmp_path / "final", [page("https://a.test/hub", "https://a.test/old")])
    changes = write_source(tmp_path / "changes" / "R", [page("https://a.test/hub", "https://a.test/new")])
    (changes / "changes.jsonl").write_text(
        json.dumps({"url": "https://a.test/hub", "change": "modified"}) + "\n", encoding="utf-8"
    )
    with WorkState.open(tmp_path / "work", "fp", crawl_id="c1") as state:
        index_inventory(final, state)
        index_inventory(changes, state)

        assert refs(state) == {("https://a.test/hub", "https://a.test/new")}


def test_resumed_changes_inventory_does_not_delete_refs_again(tmp_path: Path) -> None:
    changes = write_source(
        tmp_path / "changes" / "R",
        [page("https://a.test/hub", "https://a.test/new")],
    )
    (changes / "changes.jsonl").write_text(
        json.dumps({"url": "https://a.test/hub", "change": "modified"}) + "\n", encoding="utf-8"
    )
    with WorkState.open(tmp_path / "work", "fp", crawl_id="c1") as state:
        key = source_key(changes)
        size = (changes / "articles.jsonl").stat().st_size
        state.add_inventory_batch([], [], size, source_key=key)
        state.conn.execute(
            "INSERT INTO refs(source_url,target_url,relation) VALUES(?,?,?)",
            ("https://a.test/hub", "https://a.test/kept", "hyperlink"),
        )
        state.conn.commit()

        index_inventory(changes, state)

        assert ("https://a.test/hub", "https://a.test/kept") in refs(state)


def test_legacy_cursor_moves_to_default_source(tmp_path: Path) -> None:
    with WorkState.open(tmp_path / "work", "fp") as state:
        state.conn.execute("UPDATE inventory_cursor SET cursor=42, done=1 WHERE id=1")
        state.conn.commit()
        state.migrate_legacy_cursor("/data/crawl/final/articles.jsonl")
        state.migrate_legacy_cursor("/data/crawl/final/articles.jsonl")

        assert state.cursor("/data/crawl/final/articles.jsonl") == 42
        assert state.inventory_done("/data/crawl/final/articles.jsonl")


@pytest.mark.parametrize(
    "command", ["inventory", "download", "extract", "enrich-documents", "status"]
)
def test_cli_work_commands_accept_rebind_source(command: str) -> None:
    from ir_data.cli import parser

    assert parser().parse_args([command, "--rebind-source"]).rebind_source is True
    assert parser().parse_args([command]).rebind_source is False


@pytest.mark.parametrize("command", ["export", "build-graph", "package"])
def test_cli_output_commands_accept_rebind_source(command: str) -> None:
    from ir_data.cli import parser

    args = parser().parse_args([command, "--output", "out", "--rebind-source"])
    assert args.rebind_source is True
