from __future__ import annotations

from crawler.domain.merger.change import classify


def test_classify() -> None:
    assert classify(None, "f1") == "new"
    assert classify(None, None) == "new"
    assert classify("f1", "f1") == "unchanged"
    assert classify("f1", "f2") == "modified"
    assert classify("f1", None) == "unchanged"  # empty extraction never overwrites a good record
