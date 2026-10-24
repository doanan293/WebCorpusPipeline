from __future__ import annotations

from crawler.domain.storage.intro import MAX_INTRO_TEXTS, build_intro


def test_intro_counts_dedupes_and_caps() -> None:
    texts = ["Tuyển sinh 2026", " Tuyển  sinh 2026 ", "Xem chi tiết", "", "Tuyển sinh 2026", "A"]
    assert build_intro(texts) == "Tuyển sinh 2026\nA\nXem chi tiết"
    assert build_intro([]) == ""
    assert len(build_intro(f"t{i}" for i in range(50)).split("\n")) == MAX_INTRO_TEXTS == 20
