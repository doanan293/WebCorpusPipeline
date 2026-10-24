from __future__ import annotations

from crawler.domain.storage.intro import MAX_INTRO_TEXTS, build_intro, build_intro_from_counts


def test_intro_counts_dedupes_and_caps() -> None:
    texts = ["Tuyển sinh 2026", " Tuyển  sinh 2026 ", "Xem chi tiết", "", "Tuyển sinh 2026", "A"]
    assert build_intro(texts) == "Tuyển sinh 2026\nA\nXem chi tiết"
    assert build_intro([]) == ""
    assert len(build_intro(f"t{i}" for i in range(50)).split("\n")) == MAX_INTRO_TEXTS == 20


def test_intro_from_counts_matches_intro_from_texts() -> None:
    texts = ["B", "A", "B", " B ", "", "C"]
    counts = {"B": 2, "A": 1, " B ": 1, "": 1, "C": 1}
    assert build_intro_from_counts(counts) == build_intro(texts) == "B\nA\nC"
