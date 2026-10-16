from __future__ import annotations

from dataclasses import replace
from typing import Any, cast

from data_pipeline.crawl.application.refresh_pipeline import RefreshSession
from data_pipeline.crawl.domain.refresh_rules import RefreshSettings
from tests.fakes.crawl import FakeRefreshStore
from tests.unit.crawl.application.test_page_pipeline import page, pipeline

COUNTED_V1 = (
    "<main><h1>Thông báo</h1><p>Hạn nộp hồ sơ học bổng là ngày 16 Tháng Năm, 2024.</p>"
    "<p>Lượt xem: 5944</p></main>"
)
URL = "https://a.test/thong-bao"


def refreshing() -> tuple[Any, FakeRefreshStore, Any, str]:
    store = FakeRefreshStore()
    session = RefreshSession(cast(Any, store), "R1", RefreshSettings())
    pipe, writer = pipeline(refresh=session)
    first = {
        "url": URL,
        "final_url": URL,
        "text": pipe.text_of(COUNTED_V1.encode(), URL),
        "page_role": "content",
    }
    previous = str(writer.write_article(first)["content_id"])
    store.add_targets("R1", [(URL, "recent")])
    return pipe, store, writer, previous


def test_counter_change_is_not_modified() -> None:
    pipe, store, _, previous = refreshing()

    list(
        pipe.handle(
            page(
                URL,
                COUNTED_V1.replace("5944", "5996"),
                refresh_reason="recent",
                previous_content_id=previous,
            )
        )
    )

    assert store.outcomes[URL] == "unchanged"
    assert store.changes == []


def test_gone_page_is_reported_removed() -> None:
    pipe, store, writer, previous = refreshing()
    gone = replace(page(URL, "", refresh_reason="recent", previous_content_id=previous), status=404)

    list(pipe.handle(gone))

    assert store.outcomes[URL] == "removed"
    assert [change["change"] for change in store.changes] == ["removed"]
    assert writer.state.url_records[URL]["status"] == "absent"


def test_not_modified_closes_the_target_without_storing() -> None:
    pipe, store, writer, _ = refreshing()
    stored = len(writer.state.articles)
    unchanged = replace(page(URL, "", refresh_reason="recent"), status=304)

    list(pipe.handle(unchanged))

    assert store.outcomes[URL] == "not_modified"
    assert len(writer.state.articles) == stored
