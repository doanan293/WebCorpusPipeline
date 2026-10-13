from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from posixpath import normpath
from urllib.parse import SplitResult, parse_qsl, urlencode, urlsplit, urlunsplit

_REPEATED_SEGMENT_RUN = 4
_MAX_QUERY_PAIRS = 50
_YEAR_LENGTH = 4


@dataclass(frozen=True, slots=True)
class UrlDecision:
    accepted: bool
    canonical_url: str | None
    reason: str | None
    target_kind: str


@dataclass(frozen=True, slots=True)
class _Rejection:
    hostname: str | None
    reason: str


_LOGOUT_SEGMENTS = frozenset({"logout", "signout", "log-out"})
_TRACKING_KEYS = frozenset({"fbclid", "gclid"})
_SEARCH_KEYS = frozenset({"q", "query", "search", "keyword"})


def _split_authority(url: str) -> tuple[SplitResult, str, int | None] | _Rejection:
    try:
        parts = urlsplit(url)
    except ValueError:
        return _Rejection(None, "malformed_url")
    if parts.scheme.lower() not in {"http", "https"}:
        return _Rejection(None, "unsupported_scheme")
    if parts.username or parts.password or parts.hostname is None:
        return _Rejection(None, "credentials_or_missing_host")
    hostname = parts.hostname.lower().rstrip(".")
    try:
        port = parts.port
    except ValueError:
        return _Rejection(None, "invalid_port")
    if port and port not in {80, 443}:
        return _Rejection(hostname, "non_default_port")
    return parts, hostname, port


def _normalized_path(raw_path: str) -> str:
    path = normpath("/" + raw_path.lstrip("/"))
    if raw_path.endswith("/") and not path.endswith("/"):
        path += "/"
    return "/" if path == "/." else path


def _path_trap(lowered_path: str, segments: list[str]) -> str | None:
    if lowered_path.rstrip("/").split("/")[-1] in _LOGOUT_SEGMENTS:
        return "logout_path"
    if len(segments) >= _REPEATED_SEGMENT_RUN and len(set(segments[-_REPEATED_SEGMENT_RUN:])) == 1:
        return "repeated_path_trap"
    return None


def _clean_query(
    pairs: list[tuple[str, str]],
    *,
    trap_keys: set[str],
    lowered_path: str,
    check_traps: bool,
) -> list[tuple[str, str]] | str:
    """Drop tracking parameters; return a rejection reason when a search trap shows up."""
    cleaned: list[tuple[str, str]] = []
    for key, value in pairs:
        key_lower = key.lower()
        if key_lower in trap_keys or key_lower in _TRACKING_KEYS or key_lower.startswith("utm_"):
            continue
        if (
            check_traps
            and key_lower in _SEARCH_KEYS
            and ("search" in lowered_path or "find" in lowered_path)
        ):
            return "search_trap"
        cleaned.append((key, value))
    return cleaned


def _is_calendar_trap(segments: list[str]) -> bool:
    latest_year = datetime.now(tz=UTC).year + 2
    return any(
        segment.isdigit() and len(segment) == _YEAR_LENGTH and int(segment) > latest_year
        for segment in segments
    )


def canonicalize_url(
    url: str,
    *,
    trap_query_keys: tuple[str, ...] = ("sessionid", "jsessionid", "phpsessid"),
    check_traps: bool = True,
) -> tuple[str | None, str | None, str | None]:
    split = _split_authority(url)
    if isinstance(split, _Rejection):
        return None, split.hostname, split.reason
    parts, hostname, port = split
    scheme = parts.scheme.lower()
    netloc = hostname
    if (scheme, port) not in {("http", 80), ("https", 443), ("http", None), ("https", None)}:
        netloc = f"{hostname}:{port}"
    path = _normalized_path(parts.path)
    lowered_path = path.lower()
    segments = [segment for segment in path.split("/") if segment]
    if check_traps and (reason := _path_trap(lowered_path, segments)):
        return None, hostname, reason
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    if len(pairs) > _MAX_QUERY_PAIRS:
        return None, hostname, "too_many_query_pairs"
    cleaned = _clean_query(
        pairs,
        trap_keys={key.lower() for key in trap_query_keys},
        lowered_path=lowered_path,
        check_traps=check_traps,
    )
    if isinstance(cleaned, str):
        return None, hostname, cleaned
    if check_traps and "calendar" in lowered_path and _is_calendar_trap(segments):
        return None, hostname, "calendar_trap"
    canonical = urlunsplit((scheme, netloc, path, urlencode(sorted(cleaned)), ""))
    return canonical, hostname, None


class UrlPolicy:
    def __init__(
        self,
        allowed_hostnames: frozenset[str],
        trap_query_keys: tuple[str, ...] = ("sessionid", "jsessionid", "phpsessid"),
    ) -> None:
        self.allowed_hostnames = frozenset(allowed_hostnames)
        self.trap_query_keys = trap_query_keys

    def decide(self, url: str, *, target_kind: str = "page") -> UrlDecision:
        canonical, hostname, reason = canonicalize_url(url, trap_query_keys=self.trap_query_keys)
        if reason is not None:
            return UrlDecision(
                accepted=False, canonical_url=None, reason=reason, target_kind=target_kind
            )
        if canonical is None or hostname is None:
            msg = "canonicalize_url returned no URL without giving a reason"
            raise RuntimeError(msg)
        if hostname not in self.allowed_hostnames:
            return UrlDecision(
                accepted=False,
                canonical_url=None,
                reason="host_out_of_scope",
                target_kind=target_kind,
            )
        return UrlDecision(
            accepted=True, canonical_url=canonical, reason=None, target_kind=target_kind
        )


_AUTH_PATHS = frozenset({"login", "signin", "sign-in", "auth", "sso", "cas", "oauth"})
_ACTION_ENDPOINTS = frozenset(
    {"print", "share", "email", "submit", "comment", "cart", "checkout", "do_action"}
)


_FEED_NAMES = frozenset({"feed", "rss", "atom"})
_FEED_EXTENSIONS = (".xml", ".rss", ".atom")
_PRINT_ACTIONS = frozenset({"print", "share", "email", "export", "download"})


def _is_calendar_export(path: str, query: dict[str, str]) -> bool:
    return (
        path.endswith((".ics", ".ical"))
        or "calendar.ics" in path
        or query.get("format") in {"ical", "ics"}
        or "export_ical" in query
        or query.get("action") == "export_calendar"
    )


def _is_feed(last_segment: str) -> bool:
    if last_segment in _FEED_NAMES:
        return True
    if any(last_segment.endswith(extension) for extension in _FEED_EXTENSIONS):
        return last_segment.rsplit(".", 1)[0] in _FEED_NAMES
    return False


def classify_frontier_trap(url: str) -> str | None:
    parsed = urlsplit(url)
    path = parsed.path.lower()
    segments = [s for s in path.split("/") if s]
    last_segment = segments[-1] if segments else ""
    query = {k.lower(): v.lower() for k, v in parse_qsl(parsed.query, keep_blank_values=True)}

    if "wp-json" in segments or path.startswith("/wp-json"):
        return "api_endpoint_trap"
    if last_segment in _AUTH_PATHS:
        return "auth_trap"
    if _is_calendar_export(path, query):
        return "calendar_export_trap"
    if _is_feed(last_segment):
        return "feed_trap"
    if last_segment in {"search", "find"}:
        return "search_trap"
    if last_segment in _ACTION_ENDPOINTS or query.get("action") in _PRINT_ACTIONS:
        return "action_endpoint_trap"
    return None
