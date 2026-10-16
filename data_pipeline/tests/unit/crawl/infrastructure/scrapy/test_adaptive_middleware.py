from types import SimpleNamespace
from typing import Any, cast

from scrapy import signals
from scrapy.http import HtmlResponse, Request
from scrapy.signalmanager import SignalManager

from data_pipeline.crawl.infrastructure.scrapy.adaptive import (
    AdaptiveConcurrencyMiddleware,
    Observation,
)
from data_pipeline.crawl.infrastructure.scrapy.resources import ResourceMonitor, ResourceSampler


def _ignore(*args: Any, **kwargs: Any) -> None:
    return None


def _calm_sample() -> SimpleNamespace:
    return SimpleNamespace(cpu_percent=0.0, ram_percent=0.0)


def _slots(middleware: AdaptiveConcurrencyMiddleware) -> dict[str, Any]:
    return cast(Any, middleware._crawler).engine.downloader.slots


class _Sampler:
    """Return queued CPU readings and count how often the middleware samples."""

    def __init__(self, *cpu: float) -> None:
        self.cpu = list(cpu)
        self.calls = 0

    def sample(self) -> SimpleNamespace:
        self.calls += 1
        return SimpleNamespace(cpu_percent=self.cpu.pop(0) if self.cpu else 0.0, ram_percent=0.0)


def test_single_cpu_spike_does_not_throttle() -> None:
    middleware, _ = _middleware()
    _slots(middleware)["a.test"].concurrency = 4
    middleware._monitor = ResourceMonitor(sampler=cast(ResourceSampler, _Sampler(99.0, 20.0)))
    request = Request("https://a.test/")

    middleware._monitor.sample()
    middleware.process_response(request, HtmlResponse(request.url, status=200, request=request))

    assert not middleware._monitor.under_pressure()
    assert _slots(middleware)["a.test"].concurrency == 4


def test_sustained_pressure_throttles_and_counts_event() -> None:
    counted: list[str] = []
    middleware, crawler = _middleware()

    def count(key: str, *args: object, **kwargs: object) -> None:
        counted.append(key)

    crawler.stats = SimpleNamespace(inc_value=count, set_value=_ignore)
    _slots(middleware)["a.test"].concurrency = 4
    middleware._monitor = ResourceMonitor(
        sampler=cast(ResourceSampler, _Sampler(95.0, 96.0, 97.0)),
        on_pressure=middleware._count_pressure,
    )
    request = Request("https://a.test/")

    for _ in range(3):
        middleware._monitor.sample()
    middleware.process_response(request, HtmlResponse(request.url, status=200, request=request))

    assert middleware._monitor.under_pressure()
    assert counted.count("hust/resources/pressure") == 1
    assert _slots(middleware)["a.test"].concurrency == 1


def test_resource_sampler_not_called_per_response() -> None:
    middleware, _ = _middleware()
    sampler = _Sampler()
    middleware._monitor = ResourceMonitor(sampler=cast(ResourceSampler, sampler))
    request = Request("https://a.test/")

    for _ in range(10):
        middleware.process_response(request, HtmlResponse(request.url, status=200, request=request))

    assert sampler.calls == 0


def test_429_still_cools_down_host() -> None:
    middleware, _ = _middleware()
    _slots(middleware)["a.test"].concurrency = 4
    request = Request("https://a.test/")

    middleware.process_response(request, HtmlResponse(request.url, status=429, request=request))

    assert _slots(middleware)["a.test"].concurrency == 1
    assert _slots(middleware)["a.test"].delay >= 29.9


def _middleware() -> tuple[AdaptiveConcurrencyMiddleware, Any]:
    crawler: Any = SimpleNamespace(
        settings={
            "HUST_TUNING": SimpleNamespace(
                max_per_host=4, max_concurrency=64, resource_limit_percent=80
            )
        },
        engine=SimpleNamespace(
            downloader=SimpleNamespace(
                slots={"a.test": SimpleNamespace(concurrency=1, delay=0.25)},
            )
        ),
        stats=SimpleNamespace(inc_value=_ignore),
    )
    crawler.signals = SignalManager(crawler)
    middleware = AdaptiveConcurrencyMiddleware.from_crawler(crawler)
    middleware._monitor = ResourceMonitor(sampler=cast(ResourceSampler, _Sampler()))
    return middleware, crawler


def test_healthy_window_increases_matching_scrapy_slot() -> None:
    middleware, _ = _middleware()
    request = Request("https://a.test/")
    for _ in range(20):
        middleware.process_response(request, HtmlResponse(request.url, status=200, request=request))
    middleware.reconcile_host("a.test", request, now=31.0)
    assert _slots(middleware)["a.test"].concurrency == 2


def test_429_reduces_only_matching_slot() -> None:
    middleware, _ = _middleware()
    request = Request("https://a.test/")
    middleware.process_response(request, HtmlResponse(request.url, status=429, request=request))
    assert _slots(middleware)["a.test"].concurrency == 1


def test_429_retry_after_sets_host_cooldown() -> None:
    middleware, _ = _middleware()
    request = Request("https://a.test/")
    middleware.process_response(
        request,
        HtmlResponse(request.url, status=429, headers={b"Retry-After": b"7"}, request=request),
    )
    assert middleware._hosts["a.test"]._cooldown_until is not None
    assert _slots(middleware)["a.test"].delay >= 6.9


def test_429_without_retry_after_applies_default_delay_to_scrapy_slot() -> None:
    middleware, _ = _middleware()
    request = Request("https://a.test/")

    middleware.process_response(request, HtmlResponse(request.url, status=429, request=request))

    assert _slots(middleware)["a.test"].delay >= 29.9


def test_response_downloaded_signal_applies_cooldown_before_middleware_response() -> None:
    middleware, crawler = _middleware()
    request = Request("https://a.test/")
    response = HtmlResponse(request.url, status=429, request=request)

    crawler.signals.send_catch_log(
        signal=signals.response_downloaded,
        request=request,
        response=response,
        spider=None,
    )

    assert _slots(middleware)["a.test"].delay >= 29.9


def test_healthy_response_after_cooldown_restores_original_slot_delay() -> None:
    middleware, _ = _middleware()
    request = Request("https://a.test/")
    middleware.process_response(request, HtmlResponse(request.url, status=429, request=request))
    health = middleware._hosts["a.test"]
    assert health._cooldown_until is not None
    recovered_at = health._cooldown_until + 0.1

    health.observe(Observation(at=recovered_at, status=200, error=None))
    middleware.reconcile_host("a.test", request, now=recovered_at)

    assert _slots(middleware)["a.test"].delay == 0.25


def test_captcha_response_applies_host_circuit_cooldown() -> None:
    middleware, _ = _middleware()
    request = Request("https://a.test/article")
    response = HtmlResponse(
        request.url,
        status=200,
        headers={b"Content-Type": b"text/html"},
        body=b'<main><div class="hcaptcha"></div></main>',
        request=request,
        encoding="utf-8",
    )

    middleware.process_response(request, response)

    assert _slots(middleware)["a.test"].delay >= 299.9


def test_comment_form_captcha_does_not_open_host_circuit() -> None:
    middleware, _ = _middleware()
    request = Request("https://a.test/article")
    response = HtmlResponse(
        request.url,
        status=200,
        headers={b"Content-Type": b"text/html"},
        body=(
            b'<div id="news-body"><h1>Article</h1><p>Public content.</p></div>'
            b'<div id="formcomment" class="comment-form">'
            b'<form action="/comment/post"><img class="captchaImg" '
            b'src="/index.php?scaptcha=captcha&amp;t=123"></form></div>'
        ),
        request=request,
        encoding="utf-8",
    )

    middleware.process_response(request, response)

    assert _slots(middleware)["a.test"].delay == 0.25


def test_public_contact_form_captcha_does_not_open_host_circuit() -> None:
    middleware, _ = _middleware()
    request = Request("https://a.test/contact/")
    response = HtmlResponse(
        request.url,
        status=200,
        headers={b"Content-Type": b"text/html"},
        body=(
            b"<main><h1>Contact the faculty</h1>"
            b"<p>Our address, telephone number, and office hours are public.</p>"
            b'<form action="/contact/"><textarea name="message"></textarea>'
            b'<img class="captchaImg" src="/index.php?scaptcha=captcha&amp;t=123">'
            b"</form></main>"
        ),
        request=request,
        encoding="utf-8",
    )

    middleware.process_response(request, response)

    assert _slots(middleware)["a.test"].delay == 0.25


def test_rendered_captcha_cools_the_shared_host_slot() -> None:
    middleware, crawler = _middleware()
    request = Request("https://a.test/article", meta={"playwright": True, "rendered": True})
    response = HtmlResponse(
        request.url,
        status=200,
        headers={b"Content-Type": b"text/html"},
        body=b'<main><div class="cf-turnstile"></div></main>',
        request=request,
        encoding="utf-8",
    )

    middleware.process_response(request, response)

    assert crawler.engine.downloader.slots["a.test"].concurrency == 1
    assert crawler.engine.downloader.slots["a.test"].delay >= 299.9
    assert set(crawler.engine.downloader.slots) == {"a.test"}
