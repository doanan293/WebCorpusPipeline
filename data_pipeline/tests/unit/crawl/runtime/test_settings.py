from types import SimpleNamespace

from data_pipeline.crawl.config import CrawlerConfig
from data_pipeline.crawl.runtime.adaptive import AdaptiveConcurrencyMiddleware
from data_pipeline.crawl.runtime.settings import build_settings


def config() -> CrawlerConfig:
    return CrawlerConfig(
        hostnames=frozenset({"a.test"}),
        contact="ops@example.org",
    )


def test_settings_enable_async_concurrency_and_bounded_browser() -> None:
    values = build_settings(config())
    assert values["CONCURRENT_REQUESTS"] == 64
    assert values["CONCURRENT_REQUESTS_PER_DOMAIN"] == 4
    assert values["AUTOTHROTTLE_ENABLED"] is True
    assert values["AUTOTHROTTLE_TARGET_CONCURRENCY"] == 4.0
    assert values["RETRY_ENABLED"] is False
    assert values["PLAYWRIGHT_MAX_PAGES_PER_CONTEXT"] == 8
    assert (
        values["DOWNLOAD_HANDLERS"]["https"]
        == "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler"
    )
    assert AdaptiveConcurrencyMiddleware in values["DOWNLOADER_MIDDLEWARES"]


def test_robots_rules_are_never_applied() -> None:
    assert build_settings(config())["ROBOTSTXT_OBEY"] is False


def test_requests_use_browser_user_agent() -> None:
    assert build_settings(config())["USER_AGENT"] == config().browser_user_agent


def test_contact_sent_as_from_header() -> None:
    values = build_settings(config())

    assert values["DEFAULT_REQUEST_HEADERS"]["From"] == "ops@example.org"


def test_settings_do_not_use_scrapy_jobdir() -> None:
    assert "JOBDIR" not in build_settings(config())


def test_rendering_skips_images_media_and_fonts() -> None:
    abort = build_settings(config())["PLAYWRIGHT_ABORT_REQUEST"]

    def request(kind: str) -> SimpleNamespace:
        return SimpleNamespace(resource_type=kind)

    assert [abort(request(k)) for k in ("image", "media", "font")] == [True] * 3
    kept = ("document", "script", "stylesheet", "xhr", "fetch")
    assert [abort(request(k)) for k in kept] == [False] * len(kept)
