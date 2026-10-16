import asyncio
import socket
from typing import Any

import pytest

from data_pipeline.shared.infrastructure.network import (
    NonPublicAddressError,
    resolve_public_hostname,
    validate_public_web_url,
)


@pytest.mark.parametrize(
    "url,reason",
    [
        ("ftp://cdn.test/a.pdf", "unsupported_scheme"),
        ("https://u:p@cdn.test/a.pdf", "credentials_not_allowed"),
        ("https://cdn.test:8443/a.pdf", "non_default_port"),
        ("http://127.0.0.1/a.pdf", "non_public_address"),
        ("http://[::1]/a.pdf", "non_public_address"),
    ],
)
def test_public_web_url_rejections(url: str, reason: str) -> None:
    assert validate_public_web_url(url).reason == reason


def mixed_answers(*args: object) -> list[tuple[Any, ...]]:
    return [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.4", 443)),
    ]


def test_dns_rejects_mixed_public_private_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        mixed_answers,
    )
    with pytest.raises(NonPublicAddressError):
        asyncio.run(resolve_public_hostname("cdn.test", 443, 1.0))
