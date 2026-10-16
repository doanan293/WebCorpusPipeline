from __future__ import annotations

from dataclasses import replace
from typing import Any, cast

from data_pipeline.crawl.domain.frontier.frontier import Candidate
from data_pipeline.crawl.domain.pages import RefreshFetch
from data_pipeline.crawl.infrastructure.scrapy.requests import RequestFactory
from tests.unit.crawl.infrastructure.scrapy.test_unified import make_spider, pipeline_of


def _callback(response: Any) -> None:
    return None


def _errback(failure: Any) -> None:
    return None


def make_factory() -> RequestFactory:
    spider, _ = make_spider()
    return RequestFactory(
        options=spider.pipeline.options,
        state=spider.pipeline.state,
        callback=_callback,
        errback=_errback,
    )


def test_resume_yields_request_with_spider_callbacks() -> None:
    spider, _ = make_spider()
    record = {
        "url": "https://a.test/x",
        "response_purpose": "page",
        "discovery_source": "html_link",
        "seed_type": "recursive",
        "priority": 5,
    }

    requests = [make_factory().build(s) for s in pipeline_of(spider).resume([record])]

    assert [request.url for request in requests] == ["https://a.test/x"]
    assert requests[0].callback is _callback
    assert requests[0].errback is _errback
    assert requests[0].priority == 5


def test_resume_skips_urls_already_completed() -> None:
    spider, writer = make_spider()
    writer.state.completed.add("https://a.test/done")
    writer.state.url_records["https://a.test/done"] = {"status": "extracted"}

    scheduled = list(
        pipeline_of(spider).resume(
            [{"url": "https://a.test/done", "response_purpose": "page", "priority": 1}]
        )
    )

    assert scheduled == []


def test_candidate_rejected_by_frontier_is_not_scheduled() -> None:
    spider, writer = make_spider()
    candidate = Candidate(
        "https://other.test/page",
        "recursive",
        None,
        "page",
        explicit=False,
        discovery_source="html_link",
    )

    assert pipeline_of(spider).schedule(candidate) is None
    assert [record["reason"] for record in writer.skipped] == ["host_out_of_scope"]


def test_completed_skip_does_not_block_scheduling_final_statuses_only() -> None:
    spider, writer = make_spider()
    for url, status in (
        ("https://a.test/skipped", "skipped"),
        ("https://a.test/done", "extracted"),
    ):
        writer.state.url_records[url] = {"url": url, "status": status}
        writer.state.completed.add(url)

    def page(url: str) -> Candidate:
        return Candidate(url, "recursive", None, "page", discovery_source="html_link")

    assert pipeline_of(spider).schedule(page("https://a.test/skipped")) is not None
    assert pipeline_of(spider).schedule(page("https://a.test/done")) is None


def test_http_fallback_and_refresh_fetches_carry_their_meta() -> None:
    spider, writer = make_spider()
    factory = make_factory()
    home = Candidate("http://a.test/", "recursive", None, "page", discovery_source="seed")
    scheduled = pipeline_of(spider).schedule(home)
    assert scheduled is not None
    fallback = factory.build(replace(scheduled, http_fallback=True))
    writer.state.url_records["https://a.test/n"] = {
        "url": "https://a.test/n",
        "status": "extracted",
        "content_id": "c1",
        "etag": '"v1"',
    }
    refetch = RequestFactory(
        options=spider.pipeline.options,
        state=cast(Any, writer.state),
        callback=_callback,
        errback=_errback,
    ).build(RefreshFetch("https://a.test/n", "recent"))

    assert fallback.meta["http_fallback"] is True
    assert refetch.meta["refresh_reason"] == "recent"
    assert refetch.meta["previous_content_id"] == "c1"
    assert refetch.headers.get(b"If-None-Match") == b'"v1"'
