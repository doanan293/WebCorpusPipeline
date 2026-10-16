from __future__ import annotations

import pytest

from data_pipeline.domain.crawl.config import CrawlerConfig


def test_from_settings_names_missing_keys_without_reading_a_file() -> None:
    with pytest.raises(ValueError, match="missing required crawler settings: "):
        CrawlerConfig.from_settings({}, hostnames=frozenset({"a.test"}), contact="ops@a.test")


def test_from_settings_rejects_a_contact_that_is_not_an_email() -> None:
    with pytest.raises(ValueError, match="email"):
        CrawlerConfig.from_settings({}, hostnames=frozenset({"a.test"}), contact="not-an-email")
