import json
from pathlib import Path

from data_pipeline.application.crawl.requeue import (
    requeue_access_gates,
    requeue_retryable_failures,
)
from data_pipeline.infrastructure.crawl.sqlite.export import export_crawl
from tests.unit.infrastructure.crawl.sqlite.test_store import open_state


def test_retry_access_gates_requeues_captcha_and_login_pages(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    for url, reason in (
        ("https://a.test/false-captcha", "captcha_blocked"),
        ("https://a.test/login", "login_required"),
        ("https://a.test/feed", "feed_trap"),
    ):
        state.urls.merge_url(
            {
                "url": url,
                "status": "scheduled",
                "seed_type": "recursive",
                "frontier_action": "scheduled",
                "discovery_source": "html_link",
                "response_purpose": "page",
                "link_kind": "content",
            }
        )
        state.urls.complete_url({"url": url, "status": "skipped", "reason": reason})

    assert requeue_access_gates(state.urls) == 2
    pending = list(state.urls.iter_pending_scheduled_records())
    assert sorted(str(record["url"]) for record in pending) == [
        "https://a.test/false-captcha",
        "https://a.test/login",
    ]
    assert all(record["status"] == "scheduled" for record in pending)
    assert all("reason" not in record for record in pending)
    assert state.urls.is_complete("https://a.test/feed")
    state.close()


def test_requeue_retryable_failures_only_resets_transport_errors(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    failures = (
        (
            "https://a.test/no-route",
            "An error occurred while connecting: 113: No route to host.",
        ),
        (
            "https://a.test/timeout",
            "User timeout caused connection failure.",
        ),
        (
            "https://a.test/bad-redirect",
            "redirect_without_location",
        ),
    )
    for url, message in failures:
        state.urls.merge_url(
            {
                "url": url,
                "status": "scheduled",
                "frontier_action": "scheduled",
            }
        )
        state.urls.complete_url(
            {"url": url, "status": "failed"},
            error={"url": url, "error": "request_failed", "message": message},
        )
    state.urls.complete_url(
        {"url": "https://a.test/missing", "status": "failed"},
        error={"url": "https://a.test/missing", "error": "http_404"},
    )
    state.urls.complete_url(
        {"url": "https://a.test/bad-request", "status": "failed"},
        error={"url": "https://a.test/bad-request", "error": "http_400"},
    )
    state.urls.complete_url(
        {"url": "https://a.test/server-error", "status": "failed"},
        error={"url": "https://a.test/server-error", "error": "http_500"},
    )

    assert requeue_retryable_failures(state.urls) == 2
    pending = list(state.urls.iter_pending_scheduled_records())
    assert [record["url"] for record in pending] == [
        "https://a.test/no-route",
        "https://a.test/timeout",
    ]
    assert all(not record.get("completed") for record in pending)

    export_crawl(state.connection, tmp_path / "output")
    remaining_errors = [
        json.loads(line) for line in (tmp_path / "output/errors.jsonl").read_text().splitlines()
    ]
    assert [record["url"] for record in remaining_errors] == [
        "https://a.test/bad-redirect",
        "https://a.test/bad-request",
        "https://a.test/missing",
        "https://a.test/server-error",
    ]
    state.close()


def test_render_timeouts_are_retryable(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    messages = {
        "https://a.test/goto": 'Page.goto: Timeout 30000ms exceeded.\nCall log:\n  - navigating to "x"',
        "https://a.test/stable": "rendered body did not stabilize within 45 seconds",
    }
    for url, message in messages.items():
        state.urls.merge_url({"url": url, "status": "scheduled", "frontier_action": "scheduled"})
        state.urls.complete_url(
            {"url": url, "status": "failed"},
            error={"url": url, "error": "request_failed", "message": message},
        )

    assert requeue_retryable_failures(state.urls) == 2
    state.close()
