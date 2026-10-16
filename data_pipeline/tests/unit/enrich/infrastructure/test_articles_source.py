import json
from pathlib import Path

from data_pipeline.enrich.infrastructure.files.articles import JsonlArticleSource


def test_rows_resume_from_offset_and_report_crawl_id(tmp_path: Path) -> None:
    folder = tmp_path / "final"
    folder.mkdir()
    lines = [json.dumps({"url": f"https://a.test/{n}"}) + "\n" for n in range(3)]
    (folder / "articles.jsonl").write_text("".join(lines), encoding="utf-8")
    (folder / "README.json").write_text(json.dumps({"crawl_id": "c1"}), encoding="utf-8")
    source = JsonlArticleSource(folder)

    rows = list(source.rows(len(lines[0].encode())))

    assert [row["url"] for row, _ in rows] == ["https://a.test/1", "https://a.test/2"]
    assert rows[-1][1] == source.stat()[0]
    assert source.crawl_id() == "c1"
    assert source.key.endswith("final/articles.jsonl")
