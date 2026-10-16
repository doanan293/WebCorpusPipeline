import json
from pathlib import Path

from data_pipeline.cli.main import main
from data_pipeline.corpus.application.graph import build_graph


def source(tmp_path: Path) -> Path:
    folder = tmp_path / "final"
    folder.mkdir()
    row = {"url": "https://a.test/", "links": [{"url": "https://a.test/b", "text": "B"}]}
    (folder / "articles.jsonl").write_text(json.dumps(row) + "\n")
    return folder


def test_build_graph_needs_no_work_dir(tmp_path: Path) -> None:
    code = main(
        [
            "corpus",
            "build-graph",
            "--source",
            str(source(tmp_path)),
            "--output",
            str(tmp_path / "g"),
        ]
    )

    assert code == 0
    assert (tmp_path / "g" / "links.jsonl").read_text().count("\n") == 1
    assert not (tmp_path / "work").exists()


def test_build_graph_removes_stale_temp_db(tmp_path: Path) -> None:
    output = tmp_path / "graph"
    output.mkdir()
    (output / ".graph.sqlite3").write_text("left over by a killed run")

    counts = build_graph(source(tmp_path), output)

    assert counts["links"] == 1
    assert not (output / ".graph.sqlite3").exists()
