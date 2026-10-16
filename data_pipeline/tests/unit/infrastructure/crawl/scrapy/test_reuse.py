from __future__ import annotations

import json
from pathlib import Path

from scrapy.http import Request, Response

from data_pipeline.application.crawl.asset_responses import AssetResponses
from data_pipeline.application.crawl.writer import CrawlWriter
from data_pipeline.domain.crawl.config import CrawlerConfig
from data_pipeline.domain.crawl.frontier.assets import AssetPolicy
from data_pipeline.domain.crawl.models import ReusedAssetTarget
from data_pipeline.domain.crawl.options import CrawlOptions
from data_pipeline.infrastructure.crawl.scrapy.reuse_spider import ReusedAssetSpider
from data_pipeline.infrastructure.crawl.sqlite.unit_of_work import SqliteCrawlUnitOfWork


def make_spider(tmp_path: Path) -> tuple[ReusedAssetSpider, SqliteCrawlUnitOfWork]:
    input_path = tmp_path / "seeds.txt"
    input_path.write_text("a.test\n", encoding="utf-8")
    options = CrawlOptions(assets="all")
    state = SqliteCrawlUnitOfWork.open(
        tmp_path / "crawl-all" / "state",
        phase="crawl",
        input_path=input_path,
        semantic_config={"assets": "all"},
        runtime_config={},
    )
    writer = CrawlWriter(tmp_path / "crawl-all", state, options)
    target = ReusedAssetTarget(
        url="https://cdn.test/image?id=42",
        role="inline_image",
        group="image",
        referring_pages=("https://a.test/one", "https://a.test/two"),
    )
    spider = ReusedAssetSpider(
        config=CrawlerConfig(hostnames=frozenset({"a.test"}), contact="ops@example.org"),
        handler=AssetResponses(options=options, writer=writer, policy=AssetPolicy(options)),
        targets=(target,),
    )
    return spider, state


def test_reused_asset_spider_schedules_only_recorded_asset_urls(tmp_path: Path) -> None:
    spider, state = make_spider(tmp_path)

    requests = list(spider.start_requests())

    assert [request.url for request in requests] == ["https://cdn.test/image?id=42"]
    assert requests[0].meta["request_scope"] == "asset"
    assert requests[0].meta["asset_role"] == "inline_image"
    assert requests[0].meta["asset_group"] == "image"
    state.close()


def test_reused_asset_spider_stores_binary_and_all_referrers(tmp_path: Path) -> None:
    spider, state = make_spider(tmp_path)
    request = next(spider.start_requests())
    response = Response(
        request.url,
        status=200,
        headers={b"Content-Type": b"image/png"},
        body=b"image-body",
        request=Request(request.url, meta=request.meta),
    )

    list(spider.parse_response(response))

    payload = json.loads(state.connection.execute("SELECT payload FROM files").fetchone()[0])
    assert payload["asset_group"] == "image"
    assert payload["asset_role"] == "inline_image"
    assert payload["referring_pages"] == ["https://a.test/one", "https://a.test/two"]
    state.close()
