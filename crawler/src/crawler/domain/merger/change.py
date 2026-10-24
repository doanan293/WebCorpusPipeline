from __future__ import annotations

from crawler.domain.shared.change import Change


def classify(previous: str | None, current: str | None) -> Change:
    """Compare the page's change fingerprint with the one kept from its last visit.

    An empty extraction (``current`` is ``None``) never counts as a change, so it never overwrites
    a good record.
    """
    if previous is None:
        return "new"
    if current is None or current == previous:
        return "unchanged"
    return "modified"
