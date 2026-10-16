from __future__ import annotations

# Refresh target outcomes that count as a successful refetch.
SUCCESS_OUTCOMES = frozenset({"new", "unchanged", "modified", "not_modified", "fetched"})
# Refresh runs that a later --refresh continues instead of starting a new one.
CONTINUABLE_RUN_STATUSES = frozenset({"running", "interrupted", "failed"})
# Crawl and refresh statuses whose output is complete enough to reuse, refresh or re-extract.
FINISHED_STATUSES = frozenset({"complete", "complete_with_failures", "truncated"})
