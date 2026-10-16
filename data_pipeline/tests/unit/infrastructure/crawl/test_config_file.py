from pathlib import Path

import pytest
import yaml

from data_pipeline.domain.crawl.config import (
    CrawlerConfig,
    runtime_config_snapshot,
    semantic_config_snapshot,
    validate_network_contact,
)
from data_pipeline.domain.crawl.tuning import RuntimeTuning
from data_pipeline.infrastructure.crawl.config_file import load_crawler_config
from data_pipeline.infrastructure.shared.files import load_hostnames


@pytest.fixture(autouse=True)
def operator_contact(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRAWLER_CONTACT", "ops@example.org")


def complete_yaml(tmp_path: Path, **overrides: object) -> Path:
    values = yaml.safe_load(Path("config/crawler.yaml").read_text(encoding="utf-8"))
    values.update(overrides)
    path = tmp_path / "crawler.yaml"
    path.write_text(yaml.safe_dump(values, sort_keys=False), encoding="utf-8")
    return path


def test_load_hostnames_normalizes_and_deduplicates(tmp_path: Path) -> None:
    source = tmp_path / "domains.txt"
    source.write_text("Example.COM\n# note\nexample.com\na.example.com\n", encoding="utf-8")

    assert load_hostnames(source) == frozenset({"example.com", "a.example.com"})


def test_domain_active_uses_plain_hostname_per_line_format(tmp_path: Path) -> None:
    source = tmp_path / "domain_active.txt"
    source.write_text("b.hust.edu.vn\na.hust.edu.vn\n", encoding="utf-8")

    assert load_hostnames(source) == frozenset({"a.hust.edu.vn", "b.hust.edu.vn"})


def test_load_hostnames_rejects_urls(tmp_path: Path) -> None:
    source = tmp_path / "domains.txt"
    source.write_text("https://example.com/path\n", encoding="utf-8")

    with pytest.raises(ValueError, match="hostname"):
        load_hostnames(source)


def test_config_exposes_only_active_two_phase_fields(tmp_path: Path) -> None:
    raw = complete_yaml(tmp_path)
    config = load_crawler_config(raw, hostnames=frozenset({"a.test"}))

    removed = {
        "trap_query_keys",
        "trap_exceptions",
        "accepted_mime_types",
        "accepted_mime_prefixes",
        "media_extensions",
    }
    assert removed.isdisjoint(config.__dataclass_fields__)


def test_config_enforces_safety_defaults(tmp_path: Path) -> None:

    config = load_crawler_config(
        Path("config/crawler.yaml"),
        hostnames=frozenset({"example.com"}),
        environ={"CRAWLER_CONTACT": "ops@example.org"},
    )

    assert config.concurrent_per_host == 4
    assert config.concurrent_requests == 64
    assert config.playwright_max_pages == 8
    assert config.download_delay_seconds == 0
    assert config.throttle_start_seconds == 0.25
    assert config.autothrottle_target_concurrency == 4.0
    assert config.resource_limit_percent == 90
    assert config.max_response_bytes == 100 * 1024 * 1024


def test_config_loads_complete_runtime_policy(tmp_path: Path) -> None:
    raw = complete_yaml(
        tmp_path,
        retry_times=4,
        download_timeout_seconds=75,
    )

    config = load_crawler_config(raw, hostnames=frozenset({"example.com"}))

    assert config.retry_times == 4
    assert config.retry_statuses == (408, 425, 429, 500, 502, 503, 504)
    assert config.download_timeout_seconds == 75


def test_contact_comes_from_the_environment(tmp_path: Path) -> None:
    config = load_crawler_config(
        complete_yaml(tmp_path),
        hostnames=frozenset({"a.test"}),
        environ={"CRAWLER_CONTACT": "crawler@hust.edu.vn"},
    )

    assert config.contact == "crawler@hust.edu.vn"


@pytest.mark.parametrize(
    "contact",
    ["", "replace-with-operator-contact@example.invalid", "not an email", "ops@localhost"],
)
def test_network_contact_must_be_an_email(contact: str) -> None:
    with pytest.raises(ValueError, match="email address"):
        validate_network_contact(contact)


def test_config_load_requires_contact_in_environment(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="CRAWLER_CONTACT is required"):
        load_crawler_config(complete_yaml(tmp_path), hostnames=frozenset({"a.test"}), environ={})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("retry_times", -1),
        ("download_timeout_seconds", 0),
        ("retry_max_delay_seconds", 0),
        ("max_response_bytes", 0),
    ],
)
def test_config_rejects_non_positive_runtime_values(tmp_path: Path, field: str, value: int) -> None:
    raw = complete_yaml(tmp_path, **{field: value})

    with pytest.raises(ValueError, match=field):
        load_crawler_config(raw, hostnames=frozenset({"example.com"}))


@pytest.mark.parametrize(
    "field", ["concurrent_requests", "concurrent_per_host", "playwright_max_pages"]
)
def test_config_rejects_non_positive_concurrency_fields(tmp_path: Path, field: str) -> None:
    raw = complete_yaml(tmp_path, **{field: 0})

    with pytest.raises(ValueError, match=field):
        load_crawler_config(raw, hostnames=frozenset({"example.com"}))


def test_config_reports_all_missing_required_keys(tmp_path: Path) -> None:
    raw = tmp_path / "crawler.yaml"
    raw.write_text("assets: content-only\n", encoding="utf-8")
    with pytest.raises(ValueError) as exc_info:
        load_crawler_config(raw, hostnames=frozenset({"a.test"}))
    message = str(exc_info.value)
    assert "missing required crawler settings" in message
    assert "concurrent_requests" in message
    assert "progress_interval_seconds" in message


def test_runtime_snapshot_contains_yaml_performance_controls(tmp_path: Path) -> None:
    config = load_crawler_config(
        complete_yaml(tmp_path),
        hostnames=frozenset({"a.test"}),
    )
    tuning = RuntimeTuning(64, 2, 80, 8, 10.0)
    snapshot = runtime_config_snapshot(config, tuning)
    assert snapshot["download_timeout_seconds"] == 60.0
    assert snapshot["retry_backoff_base_seconds"] == 2.0
    assert snapshot["progress_interval_seconds"] == 10.0


@pytest.mark.parametrize("field", ["cookies_enabled", "autothrottle_enabled"])
def test_config_rejects_non_boolean_policy(tmp_path: Path, field: str) -> None:
    raw = complete_yaml(tmp_path, **{field: "yes"})
    with pytest.raises(ValueError, match=field):
        load_crawler_config(raw, hostnames=frozenset({"a.test"}))


def test_config_rejects_invalid_cross_field_limits(tmp_path: Path) -> None:
    raw = complete_yaml(
        tmp_path,
        autothrottle_target_concurrency=3.0,
        concurrent_per_host=2,
    )
    with pytest.raises(ValueError, match="autothrottle_target_concurrency"):
        load_crawler_config(raw, hostnames=frozenset({"a.test"}))


def test_config_loads_refresh_settings(tmp_path: Path) -> None:
    raw = complete_yaml(tmp_path, refresh_recent_days=30)
    config = load_crawler_config(raw, hostnames=frozenset({"a.test"}))

    assert config.refresh_pagination_empty_pages == 1
    assert config.refresh_recent_days == 30
    assert config.refresh_recent_runs == 4
    assert config.refresh_recheck_limit == 5000


def test_config_requires_refresh_settings(tmp_path: Path) -> None:
    path = complete_yaml(tmp_path)
    values = yaml.safe_load(path.read_text(encoding="utf-8"))
    del values["refresh_recheck_limit"]
    path.write_text(yaml.safe_dump(values), encoding="utf-8")

    with pytest.raises(ValueError, match="refresh_recheck_limit"):
        load_crawler_config(path, hostnames=frozenset({"a.test"}))


def test_config_rejects_non_positive_refresh_settings(tmp_path: Path) -> None:
    path = complete_yaml(tmp_path, refresh_recent_runs=0)

    with pytest.raises(ValueError, match="refresh_recent_runs must be positive"):
        load_crawler_config(path, hostnames=frozenset({"a.test"}))


def test_refresh_settings_do_not_change_crawl_identity(tmp_path: Path) -> None:
    base = load_crawler_config(complete_yaml(tmp_path), hostnames=frozenset({"a.test"}))
    changed = load_crawler_config(
        complete_yaml(tmp_path, refresh_recent_days=7),
        hostnames=frozenset({"a.test"}),
    )

    assert semantic_config_snapshot(base) == semantic_config_snapshot(changed)


def test_contact_not_in_semantic_config() -> None:
    snapshot = semantic_config_snapshot(
        CrawlerConfig(hostnames=frozenset({"a.test"}), contact="ops@example.org")
    )

    assert "contact" not in snapshot


@pytest.mark.parametrize(
    "key",
    [
        "user_agent_name",
        "user_agent_version",
        "access_policy_revision",
        "robots_txt_obey",
        "journal_batch_size",
        "journal_flush_seconds",
        "max_query_variants_per_path",
        "contact",
    ],
)
def test_removed_config_keys_rejected(tmp_path: Path, key: str) -> None:
    path = complete_yaml(tmp_path, **{key: "x"})

    with pytest.raises(ValueError, match=f"unknown crawler settings: {key}"):
        load_crawler_config(
            path, environ={"CRAWLER_CONTACT": "ops@example.org"}, hostnames=frozenset({"a.test"})
        )
