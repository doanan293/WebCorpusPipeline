from __future__ import annotations

import json
from pathlib import Path

from data_pipeline.application.corpus.graph import build_graph
from data_pipeline.cli.main import main
from data_pipeline.infrastructure.corpus.graph_index import open_edge_index


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
    rows = [{"url": "https://a.test/", "links": [{"url": "https://a.test/b", "text": "B"}]}]

    counts = build_graph(rows, output, open_edge_index)

    assert counts["links"] == 1
    assert not (output / ".graph.sqlite3").exists()


def test_build_graph_deduplicates_edges_and_lists_images(tmp_path: Path) -> None:
    link = {"url": "https://a.test/b#x", "text": "B"}
    image = {"url": "https://a.test/i.png", "role": "inline_image", "alt": "i"}
    rows = [{"url": "https://a.test/", "links": [link, link], "assets": [image]}]

    counts = build_graph(rows, tmp_path / "g", open_edge_index)

    links = [json.loads(line) for line in (tmp_path / "g" / "links.jsonl").read_text().splitlines()]
    assert counts == {"links": 2, "images": 1}
    assert [(row["target_url"], row["relation"]) for row in links] == [
        ("https://a.test/b", "hyperlink"),
        ("https://a.test/i.png", "image"),
    ]
