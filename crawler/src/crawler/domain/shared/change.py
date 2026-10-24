"""What a recrawl found, shared by the merger (which decides it) and the mediator (which acts)."""

from __future__ import annotations

from typing import Literal

# new: first time stored; modified: content changed; unchanged: same content after extraction;
# not_modified: the site answered 304 to a conditional request, nothing was downloaded.
type Change = Literal["new", "modified", "unchanged", "not_modified"]
UNCHANGED: frozenset[Change] = frozenset({"unchanged", "not_modified"})
