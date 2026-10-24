from __future__ import annotations

import json
from pathlib import Path

from data_pipeline.cli.main import main


def test_eval_extraction_reads_fixtures_and_writes_report(tmp_path: Path) -> None:
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    (fixtures / "a.html").write_text(
        "<html><body><main><h1>Tin</h1><p>Noi dung bai viet.</p></main></body></html>",
        encoding="utf-8",
    )
    (fixtures / "a.json").write_text(
        json.dumps({"url": "https://hust.edu.vn/a", "title": "Tin", "text": "Noi dung bai viet."}),
        encoding="utf-8",
    )
    output = tmp_path / "out" / "report.json"
    code = main(["eval", "extraction", "--fixtures", str(fixtures), "--output", str(output)])
    assert code == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["fixtures"] == 1
    assert report["rows"][0]["fixture"] == "a.json"
