from __future__ import annotations

from typing import Any, cast

from data_pipeline.crawl.domain.frontier.frontier import Candidate
from data_pipeline.crawl.infrastructure.scrapy.requests import RequestFactory
from tests.unit.crawl.infrastructure.scrapy.test_unified import FakeCrawlWriter, make_spider


def _callback(response: Any) -> None:
    return None


def _errback(failure: Any) -> None:
    return None


def make_factory() -> tuple[RequestFactory, FakeCrawlWriter]:
    spider, writer = make_spider()
    factory = RequestFactory(
        options=spider.options,
        frontier=spider.frontier,
        writer=cast(Any, writer),
        policy=spider.policy,
        coordinator=spider.coordinator,
        callback=_callback,
        errback=_errback,
    )
    return factory, writer


def test_resume_yields_request_with_spider_callbacks() -> None:
    factory, _ = make_factory()
    record = {
        "url": "https://a.test/x",
        "response_purpose": "page",
        "discovery_source": "html_link",
        "seed_type": "recursive",
        "priority": 5,
    }

    requests = list(factory.resume([record]))

    assert [request.url for request in requests] == ["https://a.test/x"]
    assert requests[0].callback is _callback
    assert requests[0].errback is _errback
    assert requests[0].priority == 5


def test_resume_skips_urls_already_completed() -> None:
    factory, writer = make_factory()
    writer.state.completed.add("https://a.test/done")
    writer.state.url_records["https://a.test/done"] = {"status": "extracted"}

    requests = list(
        factory.resume([{"url": "https://a.test/done", "response_purpose": "page", "priority": 1}])
    )

    assert requests == []


def test_candidate_rejected_by_frontier_returns_none() -> None:
    factory, writer = make_factory()
    candidate = Candidate(
        "https://other.test/page",
        "recursive",
        None,
        "page",
        explicit=False,
        discovery_source="html_link",
    )

    assert factory.for_candidate(candidate) is None
    assert [record["reason"] for record in writer.skipped] == ["host_out_of_scope"]


def test_completed_skip_does_not_block_scheduling_final_statuses_only() -> None:
    factory, writer = make_factory()
    for url, status in (
        ("https://a.test/skipped", "skipped"),
        ("https://a.test/done", "extracted"),
    ):
        writer.state.url_records[url] = {"url": url, "status": status}
        writer.state.completed.add(url)

    def page(url: str) -> Candidate:
        return Candidate(url, "recursive", None, "page", discovery_source="html_link")

    assert factory.for_candidate(page("https://a.test/skipped")) is not None
    assert factory.for_candidate(page("https://a.test/done")) is None
