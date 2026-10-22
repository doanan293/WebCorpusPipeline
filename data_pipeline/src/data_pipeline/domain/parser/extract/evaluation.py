from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from data_pipeline.domain.parser.extract.article import analyze_article


@dataclass(frozen=True, slots=True)
class Fixture:
    """A sample page and what extraction should find in it (``url``, ``title``, ``text``)."""

    name: str
    html: bytes
    expected: Mapping[str, Any]


def _tokens(value: str) -> set[str]:
    return {item for item in value.lower().split() if item}


def evaluate(fixtures: Iterable[Fixture]) -> dict[str, Any]:
    """Token precision, recall and F1 of the extracted text, and whether the title matches."""
    rows: list[dict[str, Any]] = []
    for fixture in fixtures:
        expected = fixture.expected
        actual = analyze_article(fixture.html, expected.get("url", "https://fixture.test/")).article
        gold = _tokens(str(expected.get("text", "")))
        pred = _tokens(str(actual.get("text", "")))
        inter = len(gold & pred)
        precision = inter / len(pred) if pred else 0
        recall = inter / len(gold) if gold else 0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0
        rows.append(
            {
                "fixture": fixture.name,
                "precision": precision,
                "recall": recall,
                "f1": f1,
                "title_ok": actual.get("title") == expected.get("title"),
            }
        )
    return {
        "fixtures": len(rows),
        "rows": rows,
        "mean_f1": sum(x["f1"] for x in rows) / len(rows) if rows else 0,
    }
