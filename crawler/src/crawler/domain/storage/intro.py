"""A page's ``intro``: what the links pointing at it say about it (the teacher's storage task 3)."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

MAX_INTRO_TEXTS = 20


def build_intro(texts: Iterable[str]) -> str:
    """Distinct link texts, most frequent first (ties by text), one per line, at most 20."""
    counts = Counter(" ".join(text.split()) for text in texts)
    counts.pop("", None)
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return "\n".join(text for text, _ in ranked[:MAX_INTRO_TEXTS])
