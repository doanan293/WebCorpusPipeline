"""A page's ``intro``: what the links pointing at it say about it (the teacher's storage task 3)."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping

MAX_INTRO_TEXTS = 20


def build_intro(texts: Iterable[str]) -> str:
    """Distinct link texts, most frequent first (ties by text), one per line, at most 20."""
    return build_intro_from_counts(Counter(texts))


def build_intro_from_counts(counts: Mapping[str, int]) -> str:
    """``build_intro`` from how often each raw link text occurs (what Mongo can count)."""
    merged: Counter[str] = Counter()
    for text, count in counts.items():
        merged[" ".join(text.split())] += count
    merged.pop("", None)
    ranked = sorted(merged.items(), key=lambda item: (-item[1], item[0]))
    return "\n".join(text for text, _ in ranked[:MAX_INTRO_TEXTS])
