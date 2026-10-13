from __future__ import annotations

from scrapy.http import Response

from data_pipeline.crawl.frontier.assets import AssetPolicy, recognizable_asset
from data_pipeline.crawl.options import CrawlOptions
from data_pipeline.crawl.output.writer import CrawlWriter, StorageLimit
from data_pipeline.crawl.spider.routing import route_response


class AssetResponseHandler:
    """Stores or skips responses that are binary assets rather than HTML pages."""

    def __init__(self, *, options: CrawlOptions, writer: CrawlWriter, policy: AssetPolicy) -> None:
        self.options = options
        self.writer = writer
        self.policy = policy

    def try_handle(self, response: Response) -> bool:
        """Handle ``response`` if it is an asset; return True when it was consumed."""
        input_url = response.meta.get("input_url", response.url)
        purpose = response.meta.get("response_purpose", "page")
        seed_mode = response.meta.get("seed_mode", "exact")
        request_scope = response.meta.get("request_scope", "page")
        discovery_source = response.meta.get("discovery_source", "seed")
        asset_role = response.meta.get("asset_role")
        content_type = response.headers.get(b"Content-Type", b"").decode("latin1", errors="replace")
        route = route_response(
            url=response.url, status=response.status, content_type=content_type, purpose=purpose
        )

        # 1. Header guard stop
        if "download_stopped" in getattr(response, "flags", []) or response.meta.get(
            "header_stop_reason"
        ):
            self.writer.record_discovered(
                {
                    "url": input_url,
                    "final_url": response.url,
                    "status": "discovered_not_downloaded",
                    "seed_type": seed_mode,
                    "discovered_from": response.meta.get("referring_page"),
                    "content_type": content_type or None,
                    "discovery_source": discovery_source,
                    "response_purpose": "asset" if request_scope == "asset" else purpose,
                    "asset_role": asset_role,
                }
            )
            self.writer.checkpoint(input_url)
            return True

        # 2. Terminal asset response
        if request_scope == "asset":
            if response.status >= 400:
                self.writer.write_error(
                    {
                        "url": input_url,
                        "final_url": response.url,
                        "error": route.error or f"http_{response.status}",
                    }
                )
                self.writer.checkpoint(input_url)
                return True

            decision = self.policy.validate_response(content_type, response.url)
            if decision.persist:
                try:
                    self.writer.write_file(
                        input_url,
                        response.url,
                        content_type,
                        response.body,
                        asset_group=decision.group or response.meta.get("asset_group", "document"),
                        asset_role=asset_role or "document_attachment",
                        referring_page=response.meta.get("referring_page"),
                    )
                except StorageLimit as exc:
                    self.writer.record_skipped(
                        {
                            "url": input_url,
                            "final_url": response.url,
                            "status": "skipped",
                            "reason": exc.reason,
                            "asset_role": asset_role,
                            "discovery_source": discovery_source,
                            "response_purpose": "asset",
                        }
                    )
            else:
                self.writer.record_skipped(
                    {
                        "url": input_url,
                        "final_url": response.url,
                        "status": "skipped",
                        "reason": decision.reason or "unsupported_asset_type",
                        "asset_role": asset_role,
                        "discovery_source": discovery_source,
                        "response_purpose": "asset",
                    }
                )
            self.writer.checkpoint(input_url)
            return True

        # 3. Ambiguous page candidate returning non-HTML
        mime = content_type.partition(";")[0].strip().lower()
        if purpose == "page" and mime and mime not in {"text/html", "application/xhtml+xml"}:
            if self.options.assets == "content-only":
                self.writer.record_discovered(
                    {
                        "url": input_url,
                        "final_url": response.url,
                        "status": "discovered_not_downloaded",
                        "seed_type": seed_mode,
                        "discovered_from": response.meta.get("referring_page"),
                        "content_type": content_type or None,
                        "discovery_source": discovery_source,
                        "response_purpose": "asset",
                        "asset_role": asset_role,
                    }
                )
                self.writer.checkpoint(input_url)
                return True
            else:
                val_decision = self.policy.validate_response(content_type, response.url)
                if val_decision.persist:
                    group = (
                        val_decision.group
                        or recognizable_asset(response.url, content_type)
                        or "document"
                    )
                    role_map = {
                        "image": "inline_image",
                        "media": "media_reference",
                        "document": "document_attachment",
                    }
                    derived_role = role_map.get(group, "document_attachment")
                    try:
                        self.writer.write_file(
                            input_url,
                            response.url,
                            content_type,
                            response.body,
                            asset_group=group,
                            asset_role=derived_role,
                            referring_page=response.meta.get("referring_page"),
                        )
                    except StorageLimit as exc:
                        self.writer.record_skipped(
                            {
                                "url": input_url,
                                "final_url": response.url,
                                "status": "skipped",
                                "reason": exc.reason,
                                "asset_role": derived_role,
                                "discovery_source": discovery_source,
                                "response_purpose": "asset",
                            }
                        )
                    self.writer.checkpoint(input_url)
                    return True
                else:
                    self.writer.record_skipped(
                        {
                            "url": input_url,
                            "final_url": response.url,
                            "status": "skipped",
                            "reason": val_decision.reason or "unsupported_asset_type",
                            "discovery_source": discovery_source,
                            "response_purpose": "asset",
                        }
                    )
                    self.writer.checkpoint(input_url)
                    return True

        return False
