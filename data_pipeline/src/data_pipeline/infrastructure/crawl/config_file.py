from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

import yaml

from data_pipeline.domain.crawl.config import CrawlerConfig


def load_crawler_config(
    config_path: Path,
    *,
    hostnames: frozenset[str],
    environ: Mapping[str, str] | None = None,
) -> CrawlerConfig:
    """Read ``crawler.yaml`` and ``CRAWLER_CONTACT`` from the environment."""
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    environment = os.environ if environ is None else environ
    contact = environment.get("CRAWLER_CONTACT", "").strip()
    if not contact:
        msg = "CRAWLER_CONTACT is required (set it in .env)"
        raise ValueError(msg)
    return CrawlerConfig.from_settings(raw, hostnames=hostnames, contact=contact)
