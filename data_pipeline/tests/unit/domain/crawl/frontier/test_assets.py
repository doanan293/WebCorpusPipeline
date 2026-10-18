import pytest

from data_pipeline.domain.crawl.frontier.assets import AssetPolicy
from data_pipeline.domain.crawl.options import CrawlOptions
from data_pipeline.domain.shared.assets import is_recognizable_binary, recognizable_asset


@pytest.mark.parametrize(
    "url,group",
    [
        ("https://cdn.test/a.svg", "image"),
        ("https://cdn.test/a.mp4", "media"),
        ("https://cdn.test/a.pdf", "document"),
        ("https://cdn.test/a.odp", "document"),
    ],
)
def test_supported_semantic_assets_are_recognized(url: str, group: str) -> None:
    assert recognizable_asset(url) == group


@pytest.mark.parametrize("url", ["/app.js", "/site.css", "/font.woff2", "/frame.html"])
def test_nonsemantic_dependencies_are_rejected(url: str) -> None:
    assert recognizable_asset(f"https://a.test{url}") is None


@pytest.mark.parametrize("url", ["/dump.zip", "/installer.exe", "/font.woff2"])
def test_known_unsupported_binary_is_recognized_without_becoming_semantic(url: str) -> None:
    absolute = f"https://a.test{url}"
    assert is_recognizable_binary(absolute) is True
    assert recognizable_asset(absolute) is None


@pytest.mark.parametrize(
    "mime,url,expected",
    [
        ("image/png", "https://x/a.bin", (True, "image", None)),
        ("application/octet-stream", "https://x/a.pdf", (True, "document", None)),
        ("text/html", "https://x/a.pdf", (False, None, "unexpected_asset_content")),
        ("application/zip", "https://x/a.zip", (False, None, "unsupported_asset_type")),
    ],
)
def test_final_response_validation(mime: str, url: str, expected: tuple[object, ...]) -> None:
    decision = AssetPolicy(CrawlOptions(assets="all")).validate_response(mime, url)
    assert (decision.persist, decision.group, decision.reason) == expected
