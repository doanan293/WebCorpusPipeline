from pathlib import Path

from data_pipeline.crawl.state.discovery import (
    RouteFamilyState,
    host_completion,
    iter_host_discovery,
    iter_route_families,
    put_route_family,
    upsert_host_discovery,
)
from tests.unit.crawl.state.test_store import open_state


def test_host_completion_reports_extracted_pages_and_terminal_reasons(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    state.complete_url({"url": "https://a.test/article", "status": "extracted"})
    state.complete_url(
        {"url": "https://b.test/", "status": "skipped", "reason": "host_out_of_scope"}
    )
    state.complete_url(
        {"url": "https://b.test/robots.txt", "status": "skipped", "reason": "robots_disallowed"}
    )

    assert host_completion(state, frozenset({"a.test", "b.test"})) == {
        "a.test": {
            "status": "complete",
            "extracted_pages": 1,
            "terminal_reasons": {},
        },
        "b.test": {
            "status": "zero_content",
            "extracted_pages": 0,
            "terminal_reasons": {
                "host_out_of_scope": 1,
                "robots_disallowed": 1,
            },
        },
    }
    state.close()


def test_host_discovery_state_round_trip(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    record = {
        "hostname": "a.test",
        "robots_complete": True,
        "pending_sitemaps": [],
        "registered_sitemaps": {"https://a.test/sitemap.xml": True},
        "sitemaps_succeeded": 1,
        "sitemaps_failed": 0,
        "sitemaps_absent": 0,
        "sitemap_targets": ["https://a.test/page1"],
        "mode": "sitemap",
    }
    upsert_host_discovery(state, record)
    state.close()

    resumed = open_state(tmp_path, resume=True)
    rows = list(iter_host_discovery(resumed))
    assert rows == [record]
    resumed.close()


def test_route_family_state_survives_resume(tmp_path: Path) -> None:
    state = open_state(tmp_path)
    family = RouteFamilyState(
        family_key="a.test/news?page={page}",
        kind="pagination",
        scheduled_next_ordinal=4,
        content_fingerprints=("c1",),
        target_fingerprints=("t1",),
        accepted_targets=("https://a.test/article/1",),
        consecutive_zero_novelty=2,
        closed=True,
        closure_reason="repeated_target_set",
    )
    put_route_family(state, family)
    assert list(iter_route_families(state)) == [family]
