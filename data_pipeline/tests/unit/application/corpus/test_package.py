import json
import stat
from contextlib import AbstractContextManager
from pathlib import Path

import pytest

from data_pipeline.application.corpus import package as corpus_package
from data_pipeline.application.corpus.package import verify_corpus
from data_pipeline.application.corpus.ports import EdgeIndex, EdgeIndexFactory
from data_pipeline.application.enrich.export import export_enrichment
from data_pipeline.application.enrich.inventory import index_inventory
from data_pipeline.domain.enrich.models import DownloadedFile, ParseResult
from data_pipeline.infrastructure.corpus.files.corpus_files import LocalCorpusFiles
from data_pipeline.infrastructure.corpus.graph_index import open_edge_index
from data_pipeline.infrastructure.enrich.files.articles import JsonlArticleSource
from data_pipeline.infrastructure.enrich.sqlite.work_state import SqliteWorkState

FILES = LocalCorpusFiles()


def package_corpus(
    source: Path,
    state: SqliteWorkState,
    output: Path,
    open_index: EdgeIndexFactory = open_edge_index,
) -> Path:
    return corpus_package.package_corpus(
        source,
        state,
        output,
        export_documents=lambda folder: export_enrichment(state, folder),
        files=FILES,
        open_index=open_index,
    )


def write_source(folder: Path, urls: list[str]) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = [
        {"url": url, "links": [{"url": f"{url}.pdf", "text": "doc"}], "assets": []} for url in urls
    ]
    (folder / "articles.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (folder / "urls.txt").write_text("".join(f"{url}\n" for url in urls))
    (folder / "README.json").write_text(json.dumps({"crawl_id": "c1"}) + "\n")
    return folder


def package(tmp_path: Path, source: Path, work: str = "work", out: str = "corpus") -> Path:
    with SqliteWorkState.open(tmp_path / work, "c1") as state:
        index_inventory(JsonlArticleSource(source), state)
        settle(state)
        return package_corpus(source, state, tmp_path / out)


def test_package_and_verify(tmp_path: Path) -> None:
    source = write_source(tmp_path / "source", ["https://example.test/a"])

    output = package(tmp_path, source)

    assert verify_corpus(output, FILES)["valid"]
    assert (output / "links.jsonl").exists() and (output / "manifest.json").exists()
    assert not (output / ".graph.sqlite3").exists()


def test_package_refuses_unresolved_enrichment(tmp_path: Path) -> None:
    source = write_source(tmp_path / "source", ["https://example.test/a"])

    with SqliteWorkState.open(tmp_path / "work", "c1") as state:
        index_inventory(JsonlArticleSource(source), state)
        with pytest.raises(ValueError, match="downloads are pending"):
            package_corpus(source, state, tmp_path / "corpus")


def test_package_refuses_without_inventory(tmp_path: Path) -> None:
    source = write_source(tmp_path / "source", ["https://example.test/a"])

    with (
        SqliteWorkState.open(tmp_path / "work", "c1") as state,
        pytest.raises(ValueError, match="inventory"),
    ):
        package_corpus(source, state, tmp_path / "corpus")


def settle(state: SqliteWorkState) -> None:
    """Mark every inventoried document as given up, so packaging is allowed."""
    for row in state.inventory_rows():
        state.record_failure(row["url"], "download", "HTTP 404")


def download_all(state: SqliteWorkState, urls: list[str]) -> None:
    """Record each document as downloaded and parsed, in the given order."""
    for url in urls:
        file = DownloadedFile(url, Path(f"{len(url)}.pdf"), "sha", 1, "application/pdf", url)
        state.mark_downloaded(file)
        state.mark_parse(url, ParseResult("sha", [], "extracted", [], {"local": "x"}))


def test_package_is_deterministic_across_insert_order(tmp_path: Path) -> None:
    urls = [f"https://example.test/{n}" for n in range(5)]
    snapshots = []
    for name, order in (("one", urls), ("two", list(reversed(urls)))):
        source = write_source(tmp_path / f"source-{name}", urls)
        with SqliteWorkState.open(tmp_path / f"work-{name}", "c1") as state:
            index_inventory(JsonlArticleSource(source), state)
            download_all(state, [f"{url}.pdf" for url in order])
            output = package_corpus(source, state, tmp_path / f"corpus-{name}")
        snapshots.append(json.loads((output / "manifest.json").read_text())["snapshot_id"])

    assert snapshots[0] == snapshots[1]


def test_report_comes_from_state_summary(tmp_path: Path) -> None:
    source = write_source(tmp_path / "source", ["https://example.test/a"])
    (tmp_path / "work").mkdir()
    (tmp_path / "work" / "extraction-report.json").write_text('{"stale": true}')
    with SqliteWorkState.open(tmp_path / "work", "c1") as state:
        index_inventory(JsonlArticleSource(source), state)
        settle(state)
        output = package_corpus(source, state, tmp_path / "corpus")

    report = json.loads((output / "extraction-report.json").read_text())
    assert "stale" not in report
    assert report["failed"] == 1


def test_package_keeps_old_output_when_copy_fails(tmp_path: Path) -> None:
    source = write_source(tmp_path / "source", ["https://example.test/a"])
    old = tmp_path / "corpus"
    old.mkdir()
    (old / "manifest.json").write_text("old")

    def fail(output: Path) -> AbstractContextManager[EdgeIndex]:
        msg = "disk full"
        raise OSError(msg)

    with SqliteWorkState.open(tmp_path / "work", "c1") as state:
        index_inventory(JsonlArticleSource(source), state)
        settle(state)
        with pytest.raises(OSError, match="disk full"):
            package_corpus(source, state, old, open_index=fail)

    assert (old / "manifest.json").read_text() == "old"
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith("corpus")] == ["corpus"]


def test_output_dir_mode_is_0755(tmp_path: Path) -> None:
    source = write_source(tmp_path / "source", ["https://example.test/a"])
    with SqliteWorkState.open(tmp_path / "work", "c1") as state:
        index_inventory(JsonlArticleSource(source), state)
        settle(state)
        output = package_corpus(source, state, tmp_path / "corpus")

    assert stat.S_IMODE(output.stat().st_mode) == 0o755


def test_manifest_counts_lines(tmp_path: Path) -> None:
    source = write_source(tmp_path / "source", ["https://example.test/a", "https://example.test/b"])
    with SqliteWorkState.open(tmp_path / "work", "c1") as state:
        index_inventory(JsonlArticleSource(source), state)
        settle(state)
        output = package_corpus(source, state, tmp_path / "corpus")

    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["counts"]["articles.jsonl"] == 2
    assert manifest["counts"]["documents.jsonl"] == 0
    assert "status" not in manifest


def test_package_names_cloud_failures(tmp_path: Path) -> None:
    source = write_source(tmp_path / "source", ["https://example.test/a"])
    with SqliteWorkState.open(tmp_path / "work", "c1") as state:
        index_inventory(JsonlArticleSource(source), state)
        download_all(state, ["https://example.test/a.pdf"])
        failed = ParseResult("sha", [], "cloud_failed", ["transient: boom"], {})
        state.mark_parse("https://example.test/a.pdf", failed, used_cloud=True)

        with pytest.raises(ValueError, match="1 documents failed in LlamaParse"):
            package_corpus(source, state, tmp_path / "corpus")
