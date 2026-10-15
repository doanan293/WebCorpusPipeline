import json
import stat
from pathlib import Path

import pytest

from data_pipeline.corpus import package as corpus_package
from data_pipeline.corpus.package import package_corpus, verify_corpus
from data_pipeline.enrich.inventory import index_inventory
from data_pipeline.enrich.models import DownloadedFile, ParseResult
from data_pipeline.enrich.state import WorkState


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
    with WorkState.open(tmp_path / work, "c1") as state:
        index_inventory(source, state)
        settle(state)
        return package_corpus(source, state, tmp_path / out)


def test_package_and_verify(tmp_path: Path) -> None:
    source = write_source(tmp_path / "source", ["https://example.test/a"])

    output = package(tmp_path, source)

    assert verify_corpus(output)["valid"]
    assert (output / "links.jsonl").exists() and (output / "manifest.json").exists()
    assert not (output / ".graph.sqlite3").exists()


def test_package_refuses_unresolved_enrichment(tmp_path: Path) -> None:
    source = write_source(tmp_path / "source", ["https://example.test/a"])

    with WorkState.open(tmp_path / "work", "c1") as state:
        index_inventory(source, state)
        with pytest.raises(ValueError, match="downloads are pending"):
            package_corpus(source, state, tmp_path / "corpus")


def test_package_refuses_without_inventory(tmp_path: Path) -> None:
    source = write_source(tmp_path / "source", ["https://example.test/a"])

    with (
        WorkState.open(tmp_path / "work", "c1") as state,
        pytest.raises(ValueError, match="inventory"),
    ):
        package_corpus(source, state, tmp_path / "corpus")


def settle(state: WorkState) -> None:
    """Mark every inventoried document as given up, so packaging is allowed."""
    for row in state.inventory_rows():
        state.record_failure(row["url"], "download", "HTTP 404")


def download_all(state: WorkState, urls: list[str]) -> None:
    """Record each document as downloaded and parsed, in the given order."""
    for url in urls:
        file = DownloadedFile(url, Path(f"{len(url)}.pdf"), "sha", 1, "application/pdf", url)
        state.mark_downloaded(file)
        state.mark_parse(url, ParseResult("sha", [], "extracted", [], {"local": "x"}))


def test_package_is_deterministic_across_insert_order(tmp_path: Path) -> None:
    urls = [f"https://example.test/{n}" for n in range(5)]
    snapshots = []
    for name, order in (("one", urls), ("two", list(reversed(urls)))):
        source = write_source(tmp_path / f"source-{name}", order)
        with WorkState.open(tmp_path / f"work-{name}", "c1") as state:
            index_inventory(source, state)
            download_all(state, [f"{url}.pdf" for url in order])
            (source / "articles.jsonl").write_text(
                (tmp_path / "source-one" / "articles.jsonl").read_text()
            )
            (source / "urls.txt").write_text((tmp_path / "source-one" / "urls.txt").read_text())
            output = package_corpus(source, state, tmp_path / f"corpus-{name}")
        snapshots.append(json.loads((output / "manifest.json").read_text())["snapshot_id"])

    assert snapshots[0] == snapshots[1]


def test_report_comes_from_state_summary(tmp_path: Path) -> None:
    source = write_source(tmp_path / "source", ["https://example.test/a"])
    (tmp_path / "work").mkdir()
    (tmp_path / "work" / "extraction-report.json").write_text('{"stale": true}')
    with WorkState.open(tmp_path / "work", "c1") as state:
        index_inventory(source, state)
        settle(state)
        output = package_corpus(source, state, tmp_path / "corpus")

    report = json.loads((output / "extraction-report.json").read_text())
    assert "stale" not in report
    assert report["failed"] == 1


def test_package_keeps_old_output_when_copy_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = write_source(tmp_path / "source", ["https://example.test/a"])
    old = tmp_path / "corpus"
    old.mkdir()
    (old / "manifest.json").write_text("old")

    def fail(*args: object, **kwargs: object) -> None:
        msg = "disk full"
        raise OSError(msg)

    monkeypatch.setattr(corpus_package, "build_graph", fail)
    with WorkState.open(tmp_path / "work", "c1") as state:
        index_inventory(source, state)
        settle(state)
        with pytest.raises(OSError, match="disk full"):
            package_corpus(source, state, old)

    assert (old / "manifest.json").read_text() == "old"
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith("corpus")] == ["corpus"]


def test_output_dir_mode_is_0755(tmp_path: Path) -> None:
    source = write_source(tmp_path / "source", ["https://example.test/a"])
    with WorkState.open(tmp_path / "work", "c1") as state:
        index_inventory(source, state)
        settle(state)
        output = package_corpus(source, state, tmp_path / "corpus")

    assert stat.S_IMODE(output.stat().st_mode) == 0o755


def test_manifest_counts_lines(tmp_path: Path) -> None:
    source = write_source(tmp_path / "source", ["https://example.test/a", "https://example.test/b"])
    with WorkState.open(tmp_path / "work", "c1") as state:
        index_inventory(source, state)
        settle(state)
        output = package_corpus(source, state, tmp_path / "corpus")

    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["counts"]["articles.jsonl"] == 2
    assert manifest["counts"]["documents.jsonl"] == 0
    assert "status" not in manifest
