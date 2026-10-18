from __future__ import annotations

from dataclasses import dataclass

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)


class FetchError(Exception):
    """A URL could not be fetched; the message is a short reason such as ``too_large``."""


@dataclass(frozen=True, slots=True)
class Fetched:
    final_url: str
    status: int
    content_type: str
    body: bytes


@dataclass(frozen=True, slots=True)
class FetcherConfig:
    port: int = 8800
    user_agent: str = DEFAULT_USER_AGENT
    timeout_seconds: float = 60.0
    max_redirects: int = 20
    max_response_bytes: int = 104857600
    max_file_bytes: int = 104857600
    allow_private_addresses: bool = False
