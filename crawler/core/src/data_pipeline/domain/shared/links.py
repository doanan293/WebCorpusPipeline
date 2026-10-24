from __future__ import annotations

from urllib.parse import quote, unquote_plus, urljoin, urlsplit, urlunsplit

_TRACKING_KEYS = {
    "_ga",
    "_gl",
    "dclid",
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "msclkid",
}


_TRACKING_PREFIXES = ("utm_",)


def _clean_query(query: str) -> str:
    kept: list[str] = []
    for field in query.split("&"):
        raw_key = field.partition("=")[0]
        key = unquote_plus(raw_key).lower()
        if key in _TRACKING_KEYS or any(key.startswith(prefix) for prefix in _TRACKING_PREFIXES):
            continue
        kept.append(field)
    return "&".join(kept)


def normalize_url(value: str, base_url: str | None = None) -> str | None:
    """Resolve and normalize a web URL without rewriting its meaningful query data."""
    value = value.strip()
    if not value or any(character in value for character in "\x00\r\n"):
        return None
    try:
        resolved = urljoin(base_url, value) if base_url else value
        parts = urlsplit(resolved)
        hostname = parts.hostname
        port = parts.port
    except (TypeError, ValueError):
        return None
    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"} or hostname is None:
        return None
    if parts.username is not None or parts.password is not None:
        return None

    hostname = hostname.lower().rstrip(".")
    if not hostname or any(character.isspace() for character in hostname):
        return None
    rendered_host = f"[{hostname}]" if ":" in hostname else hostname
    if port is not None and (scheme, port) not in {("http", 80), ("https", 443)}:
        rendered_host = f"{rendered_host}:{port}"

    path = quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~")
    query = quote(_clean_query(parts.query), safe="!$&'()*+,-./:;=?@_%~")
    return urlunsplit((scheme, rendered_host, path, query, ""))
