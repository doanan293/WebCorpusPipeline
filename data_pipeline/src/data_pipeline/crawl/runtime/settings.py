from __future__ import annotations

from typing import Any

from data_pipeline.crawl.config import CrawlerConfig
from data_pipeline.crawl.runtime.adaptive import AdaptiveConcurrencyMiddleware
from data_pipeline.crawl.runtime.middlewares import (
    ContentOnlyHeadersGuard,
    PlaywrightFallbackMiddleware,
    PoliteRetryMiddleware,
    PublicAssetAddressMiddleware,
    SafeRedirectMiddleware,
)
from data_pipeline.crawl.runtime.playwright_driver import isolate_playwright_driver
from data_pipeline.crawl.runtime.profiles import RuntimeTuning, resolve_runtime_tuning


def build_settings(
    config: CrawlerConfig,
    *,
    tuning: RuntimeTuning | None = None,
    extensions: dict[object, int] | None = None,
) -> dict[str, Any]:
    tuning = tuning or resolve_runtime_tuning(config=config)
    isolate_playwright_driver()
    settings: dict[str, Any] = {
        # Robots rules are not applied; robots.txt is only read for its Sitemap lines.
        "ROBOTSTXT_OBEY": False,
        "USER_AGENT": config.browser_user_agent,
        "DEFAULT_REQUEST_HEADERS": {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "vi,en;q=0.8",
            "From": config.contact,
        },
        "COOKIES_ENABLED": config.cookies_enabled,
        "CONCURRENT_REQUESTS": tuning.max_concurrency,
        "CONCURRENT_REQUESTS_PER_DOMAIN": tuning.max_per_host,
        "DOWNLOAD_DELAY": config.download_delay_seconds,
        "AUTOTHROTTLE_ENABLED": config.autothrottle_enabled,
        "AUTOTHROTTLE_START_DELAY": config.throttle_start_seconds,
        "AUTOTHROTTLE_MAX_DELAY": config.throttle_max_seconds,
        "AUTOTHROTTLE_TARGET_CONCURRENCY": config.autothrottle_target_concurrency,
        "RETRY_ENABLED": False,
        "DOWNLOAD_MAXSIZE": config.max_response_bytes,
        "DOWNLOAD_WARNSIZE": config.max_response_bytes,
        "DOWNLOAD_TIMEOUT": config.download_timeout_seconds,
        "REDIRECT_MAX_TIMES": config.redirect_max_times,
        "HTTPERROR_ALLOW_ALL": True,
        "REDIRECT_ENABLED": False,
        "TELNETCONSOLE_ENABLED": False,
        "LOG_FORMAT": "%(message)s",
        "TWISTED_REACTOR": "twisted.internet.asyncioreactor.AsyncioSelectorReactor",
        "DOWNLOAD_HANDLERS": {
            "http": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
            "https": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
        },
        "PLAYWRIGHT_BROWSER_TYPE": "chromium",
        "PLAYWRIGHT_MAX_PAGES_PER_CONTEXT": tuning.playwright_max_pages,
        "DOWNLOADER_MIDDLEWARES": {
            # Asset DNS safety must run before Scrapy's RobotsTxtMiddleware
            # (priority 100), otherwise Scrapy can fetch the asset host's
            # robots.txt before the host is proven public.
            PublicAssetAddressMiddleware: 50,
            SafeRedirectMiddleware: 550,
            PoliteRetryMiddleware: 560,
            PlaywrightFallbackMiddleware: 570,
            AdaptiveConcurrencyMiddleware: 580,
        },
        "HUST_CONFIG": config,
        "HUST_TUNING": tuning,
    }
    ext_dict: dict[object, int] = {ContentOnlyHeadersGuard: 100}
    if extensions:
        ext_dict.update(extensions)
    settings["EXTENSIONS"] = ext_dict
    return settings
