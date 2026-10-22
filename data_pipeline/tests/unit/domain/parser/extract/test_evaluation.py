from __future__ import annotations

from data_pipeline.domain.parser.extract.evaluation import Fixture, evaluate

PAGE = (
    b"<html><head><title>Tin 1</title></head><body><main><h1>Tin 1</h1>"
    b"<p>Sinh vien Bach khoa nhan hoc bong nam nay.</p></main></body></html>"
)


def test_evaluate_scores_each_fixture() -> None:
    expected = {"url": "https://hust.edu.vn/a", "title": "Tin 1", "text": "Sinh vien nhan hoc bong"}
    report = evaluate([Fixture("a.json", PAGE, expected)])
    assert report["fixtures"] == 1
    (row,) = report["rows"]
    assert row["fixture"] == "a.json"
    assert row["title_ok"] is True
    assert row["recall"] == 1.0
    assert 0 < row["precision"] < 1
    assert report["mean_f1"] == row["f1"]


def test_evaluate_without_fixtures() -> None:
    assert evaluate([]) == {"fixtures": 0, "rows": [], "mean_f1": 0}
