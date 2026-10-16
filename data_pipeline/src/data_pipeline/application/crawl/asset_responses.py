from __future__ import annotations

from collections.abc import Iterable, Iterator
from http import HTTPStatus

from data_pipeline.application.crawl.writer import CrawlWriter, StorageLimitError
from data_pipeline.domain.crawl.frontier.assets import (
    AssetPolicy,
    recognizable_asset,
    role_for_group,
)
from data_pipeline.domain.crawl.models import ReusedAssetTarget
from data_pipeline.domain.crawl.options import CrawlOptions
from data_pipeline.domain.crawl.pages import FailedRequest, FetchedPage
from data_pipeline.domain.crawl.routing import route_response

_ACCESS_GATES = ("captcha_blocked", "login_required")
_ASSET_SAFETY_REASONS = {
    "non_public_address",
    "unsupported_scheme",
    "credentials_not_allowed",
    "non_default_port",
    "redirect_without_location",
    "redirect_limit",
    "redirect_loop",
}


class AssetResponses:
    """Stores or skips responses that are binary assets rather than HTML pages."""

    def __init__(self, *, options: CrawlOptions, writer: CrawlWriter, policy: AssetPolicy) -> None:
        self.options = options
        self.writer = writer
        self.policy = policy

    def try_handle(self, page: FetchedPage) -> bool:
        """Handle ``page`` if it is an asset; return True when it was consumed."""
        meta = page.meta
        input_url = page.input_url
        purpose = page.purpose
        request_scope = meta.get("request_scope", "page")
        discovery_source = page.discovery_source
        asset_role = meta.get("asset_role")
        content_type = page.content_type
        route = route_response(
            url=page.url, status=page.status, content_type=content_type, purpose=purpose
        )

        # 1. Header guard stop
        if "download_stopped" in page.flags or meta.get("header_stop_reason"):
            self.writer.record_discovered(
                {
                    "url": input_url,
                    "final_url": page.url,
                    "status": "discovered_not_downloaded",
                    "seed_type": page.seed_mode,
                    "discovered_from": meta.get("discovered_from"),
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
            if page.status >= HTTPStatus.BAD_REQUEST:
                self.writer.write_error(
                    {
                        "url": input_url,
                        "final_url": page.url,
                        "error": route.error or f"http_{page.status}",
                    }
                )
                self.writer.checkpoint(input_url)
                return True

            decision = self.policy.validate_response(content_type, page.url)
            if decision.persist:
                try:
                    self.writer.write_file(
                        input_url,
                        page.url,
                        content_type,
                        page.body,
                        asset_group=decision.group or meta.get("asset_group", "document"),
                        asset_role=asset_role or "document_attachment",
                        referring_page=meta.get("referring_page"),
                    )
                except StorageLimitError as exc:
                    self.writer.record_skipped(
                        {
                            "url": input_url,
                            "final_url": page.url,
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
                        "final_url": page.url,
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
                        "final_url": page.url,
                        "status": "discovered_not_downloaded",
                        "seed_type": page.seed_mode,
                        "discovered_from": meta.get("discovered_from"),
                        "content_type": content_type or None,
                        "discovery_source": discovery_source,
                        "response_purpose": "asset",
                        "asset_role": asset_role,
                    }
                )
                self.writer.checkpoint(input_url)
                return True
            val_decision = self.policy.validate_response(content_type, page.url)
            if val_decision.persist:
                group = (
                    val_decision.group or recognizable_asset(page.url, content_type) or "document"
                )
                derived_role = role_for_group(group)
                try:
                    self.writer.write_file(
                        input_url,
                        page.url,
                        content_type,
                        page.body,
                        asset_group=group,
                        asset_role=derived_role,
                        referring_page=meta.get("discovered_from"),
                    )
                except StorageLimitError as exc:
                    self.writer.record_skipped(
                        {
                            "url": input_url,
                            "final_url": page.url,
                            "status": "skipped",
                            "reason": exc.reason,
                            "asset_role": derived_role,
                            "discovery_source": discovery_source,
                            "response_purpose": "asset",
                        }
                    )
                self.writer.checkpoint(input_url)
                return True
            self.writer.record_skipped(
                {
                    "url": input_url,
                    "final_url": page.url,
                    "status": "skipped",
                    "reason": val_decision.reason or "unsupported_asset_type",
                    "discovery_source": discovery_source,
                    "response_purpose": "asset",
                }
            )
            self.writer.checkpoint(input_url)
            return True

        return False

    def handle_failure(self, failure: FailedRequest) -> bool:
        """Record an access-gated or asset request failure; return True when it was consumed."""
        input_url = failure.input_url
        error = failure.error
        access_reason = next(
            (reason for reason in _ACCESS_GATES if error.startswith(f"{reason}:")), None
        )
        if failure.ignored and access_reason is not None:
            self.writer.record_skipped(
                {"url": input_url, "status": "skipped", "reason": access_reason}
            )
            self.writer.checkpoint(input_url)
            return True

        if failure.request_scope == "asset" and failure.ignored:
            matched_reason = next((r for r in _ASSET_SAFETY_REASONS if r in error), None)
            if matched_reason:
                self.writer.record_skipped(
                    {
                        "url": input_url,
                        "final_url": failure.url,
                        "status": "skipped",
                        "reason": matched_reason,
                        "discovery_source": failure.discovery_source,
                        "response_purpose": "asset",
                    }
                )
                self.writer.checkpoint(input_url)
                return True

        if failure.request_scope == "asset":
            self.writer.write_error(
                {
                    "url": input_url,
                    "final_url": failure.url,
                    "error": "request_failed",
                    "message": error,
                }
            )
            self.writer.checkpoint(input_url)
            return True
        return False

    def reused(self, targets: Iterable[ReusedAssetTarget]) -> Iterator[ReusedAssetTarget]:
        """Record each reused asset target as scheduled, then hand it on to be fetched."""
        for target in targets:
            for referring_page in target.referring_pages:
                self.writer.state.add_asset_referrer(target.url, referring_page, target.role)
            self.writer.record_scheduled(
                {
                    "url": target.url,
                    "status": "scheduled",
                    "seed_type": "exact",
                    "discovered_from": (
                        target.referring_pages[0] if target.referring_pages else None
                    ),
                    "frontier_action": "scheduled",
                    "discovery_source": "reused_content",
                    "response_purpose": "asset",
                }
            )
            yield target
