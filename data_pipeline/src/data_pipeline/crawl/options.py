from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

from data_pipeline.crawl.config import CrawlerConfig
from data_pipeline.crawl.frontier.seeds import SeedSet

AssetMode = Literal["content-only", "all"]


@dataclass(frozen=True, slots=True)
class CrawlOptions:
    assets: AssetMode = "content-only"
    max_file_bytes: int = 100 * 1024 * 1024
    max_total_file_bytes: int = 100 * 1024**3
    pagination_empty_pages: int = 3
    max_urls_per_host: int = 100_000
    max_total_urls: int = 1_000_000

    def __post_init__(self) -> None:
        if self.assets not in {"content-only", "all"}:
            msg = "assets must be content-only or all"
            raise ValueError(msg)
        for name, value in asdict(self).items():
            if name != "assets" and int(value) <= 0:
                msg = f"{name} must be positive"
                raise ValueError(msg)

    def semantic_snapshot(self, seeds: SeedSet | None = None) -> dict[str, object]:
        return {
            **asdict(self),
            "recursive_hostnames": sorted(seeds.recursive_hostnames) if seeds else [],
            "exact_urls": list(seeds.exact_urls) if seeds else [],
        }


def crawl_options_from_config(config: CrawlerConfig, **overrides: object) -> CrawlOptions:
    names = (
        "assets",
        "max_file_bytes",
        "max_total_file_bytes",
        "pagination_empty_pages",
        "max_urls_per_host",
        "max_total_urls",
    )
    values: dict[str, Any] = {
        name: overrides[name] if overrides.get(name) is not None else getattr(config, name)
        for name in names
    }
    return CrawlOptions(**values)
