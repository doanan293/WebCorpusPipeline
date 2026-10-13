from __future__ import annotations

import json
from pathlib import Path

from data_pipeline.crawl.extract.article import analyze_article


def _tokens(value: str) -> set[str]:
    return {item for item in value.lower().split() if item}


def evaluate(fixtures: Path, output: Path) -> dict:
    rows = []
    for expected_path in sorted(fixtures.glob("*.json")):
        expected = json.loads(expected_path.read_text())
        html = (expected_path.with_suffix(".html")).read_bytes()
        actual = analyze_article(html, expected.get("url", "https://fixture.test/")).article
        gold = _tokens(expected.get("text", ""))
        pred = _tokens(actual.get("text", ""))
        inter = len(gold & pred)
        precision = inter / len(pred) if pred else 0
        recall = inter / len(gold) if gold else 0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0
        rows.append(
            {
                "fixture": expected_path.name,
                "precision": precision,
                "recall": recall,
                "f1": f1,
                "title_ok": actual.get("title") == expected.get("title"),
            }
        )
    result = {
        "fixtures": len(rows),
        "rows": rows,
        "mean_f1": sum(x["f1"] for x in rows) / len(rows) if rows else 0,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result
