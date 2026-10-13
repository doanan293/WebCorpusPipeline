import json
from pathlib import Path

from data_pipeline.crawl.state.export import (
    export,
    reason_counts,
)
from data_pipeline.crawl.state.requeue import (
    requeue_access_gates,
    requeue_policy_skips,
    requeue_query_variant_truncations,
    requeue_retryable_failures,
    requeue_zero_content_bootstrap,
)
from tests.unit.crawl.state.test_store import open_state


def test_requeue_policy_skips_preserves_successful_records_and_is_idempotent(
    tmp_path: Path,
) -> None:
    state = open_state(tmp_path)
    successful = {
        "url": "https://a.test/success",
        "status": "extracted",
        "title": "Keep me",
    }
    state.complete_url(successful, article={"url": successful["url"], "title": "Keep me"})
    state.complete_url(
        {"url": "https://a.test/file.pdf", "status": "file_saved"},
        file={"url": "https://a.test/file.pdf", "path": "files/file.pdf"},
    )
    for reason in ("robots_disallowed", "login_required", "captcha_blocked", "access_denied"):
        url = f"https://a.test/{reason}"
        state.complete_url(
            {"url": url, "status": "skipped", "reason": reason},
            error={"url": url, "error": reason},
        )
    state.complete_url(
        {"url": "https://a.test/not-found", "status": "failed", "reason": "http_404"},
        error={"url": "https://a.test/not-found", "error": "http_404"},
    )

    assert requeue_policy_skips(state, frozenset({"a.test"})) == 4
    pending = {row["url"] for row in state.iter_pending_scheduled_records()}
    assert pending == {
        "https://a.test/robots_disallowed",
        "https://a.test/login_required",
        "https://a.test/captcha_blocked",
        "https://a.test/access_denied",
    }
    assert state.is_complete("https://a.test/success")
    assert state.is_complete("https://a.test/file.pdf")
    article_payload = state.connection.execute(
        "SELECT payload FROM articles WHERE url = ?",
        ("https://a.test/success",),
    ).fetchone()[0]
    assert json.loads(article_payload) == {"url": "https://a.test/success", "title": "Keep me"}
    assert requeue_policy_skips(state, frozenset({"a.test"})) == 0
    state.close()


def test_requeue_zero_content_bootstrap_only_resets_recoverable_roots(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    state.complete_url({"url": "https://a.test/", "status": "skipped", "reason": "access_denied"})
    state.complete_url({"url": "https://b.test/", "status": "extracted"})
    state.complete_url(
        {"url": "https://c.test/", "status": "skipped", "reason": "host_out_of_scope"}
    )

    assert requeue_zero_content_bootstrap(state, frozenset({"a.test", "b.test", "c.test"})) == 1
    pending = {row["url"] for row in state.iter_pending_scheduled_records()}
    assert "https://a.test/" in pending
    assert "https://b.test/" not in pending
    assert "https://c.test/" not in pending
    state.close()


def test_requeue_access_gates_only_resets_captcha_blocked_pages(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    for url, reason in (
        ("https://a.test/false-captcha", "captcha_blocked"),
        ("https://a.test/login", "login_required"),
        ("https://a.test/query", "query_variant_limit"),
    ):
        state.merge_url(
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
        state.complete_url({"url": url, "status": "skipped", "reason": reason})

    assert requeue_access_gates(state) == 1
    pending = list(state.iter_pending_scheduled_records())
    assert [record["url"] for record in pending] == ["https://a.test/false-captcha"]
    assert pending[0]["status"] == "scheduled"
    assert "reason" not in pending[0]
    assert state.is_complete("https://a.test/login")
    assert state.is_complete("https://a.test/query")
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
    for index, (url, message) in enumerate(failures):
        state.merge_url(
            {
                "url": url,
                "status": "scheduled",
                "frontier_action": "scheduled",
            }
        )
        state.complete_url(
            {"url": url, "status": "failed", "completed": index == 0},
            error={"url": url, "error": "request_failed", "message": message},
        )
    state.complete_url(
        {"url": "https://a.test/missing", "status": "failed"},
        error={"url": "https://a.test/missing", "error": "http_404"},
    )
    state.complete_url(
        {"url": "https://a.test/bad-request", "status": "failed"},
        error={"url": "https://a.test/bad-request", "error": "http_400"},
    )
    state.complete_url(
        {"url": "https://a.test/server-error", "status": "failed"},
        error={"url": "https://a.test/server-error", "error": "http_500"},
    )

    assert requeue_retryable_failures(state) == 2
    pending = list(state.iter_pending_scheduled_records())
    assert [record["url"] for record in pending] == [
        "https://a.test/no-route",
        "https://a.test/timeout",
    ]
    assert all(not record.get("completed") for record in pending)

    export(state, tmp_path / "output")
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


def test_requeue_query_variant_truncations_rebuilds_semantic_metadata(
    tmp_path: Path,
) -> None:
    state = open_state(tmp_path)
    records = (
        {
            "url": "https://wp.test/?p=33214",
            "final_url": "https://wp.test/?p=33214",
            "status": "skipped",
            "reason": "query_variant_limit",
            "discovery_source": "html_link",
            "response_purpose": "page",
        },
        {
            "url": "https://a.test/news?page=22&tag=undergraduate",
            "final_url": "https://a.test/news?page=22&tag=undergraduate",
            "status": "skipped",
            "reason": "query_variant_limit",
            "discovery_source": "html_link",
            "response_purpose": "page",
        },
        {
            "url": "https://a.test/over-host-budget",
            "status": "skipped",
            "reason": "host_url_limit",
        },
        {
            "url": "https://a.test/news?sort=oldest",
            "status": "skipped",
            "reason": "query_variant_limit",
            "discovery_source": "html_link",
            "response_purpose": "page",
        },
    )
    for record in records:
        state.complete_url(record)

    assert requeue_query_variant_truncations(state) == 2
    assert requeue_query_variant_truncations(state) == 0

    pending = list(state.iter_pending_scheduled_records())
    assert [record["url"] for record in pending] == [
        "https://a.test/news?page=22&tag=undergraduate",
        "https://wp.test/?p=33214",
    ]
    pagination, wordpress = pending
    assert pagination["link_kind"] == "pagination"
    assert pagination["family_key"] == ("a.test/news?page={page}&tag=undergraduate")
    assert pagination["ordinal"] == 22
    assert wordpress["link_kind"] == "content"
    assert wordpress.get("family_key") is None
    assert wordpress.get("ordinal") is None
    assert all(record["status"] == "scheduled" for record in pending)
    assert all("reason" not in record for record in pending)
    assert reason_counts(state, frozenset({"query_variant_limit"})) == {"query_variant_limit": 1}
    assert reason_counts(state, frozenset({"host_url_limit"})) == {"host_url_limit": 1}
    state.close()
