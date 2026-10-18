from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import urlsplit

from data_pipeline.domain.crawl.options import CrawlOptions
from data_pipeline.domain.shared.assets import DOCUMENT_MIME_TYPES, EXTENSION_GROUPS, AssetGroup


@dataclass(frozen=True, slots=True)
class AssetResponseDecision:
    persist: bool
    group: AssetGroup | None
    reason: str | None


class AssetPolicy:
    def __init__(self, options: CrawlOptions | None = None) -> None:
        self.options = options if options is not None else CrawlOptions()

    def validate_response(self, content_type: str, final_url: str) -> AssetResponseDecision:
        mime = content_type.partition(";")[0].strip().lower()
        if mime in {"text/html", "application/xhtml+xml"} or mime.startswith("text/html"):
            return AssetResponseDecision(
                persist=False, group=None, reason="unexpected_asset_content"
            )

        if mime.startswith("image/"):
            return AssetResponseDecision(persist=True, group="image", reason=None)
        if mime.startswith(("audio/", "video/")):
            return AssetResponseDecision(persist=True, group="media", reason=None)
        if mime in DOCUMENT_MIME_TYPES:
            return AssetResponseDecision(persist=True, group="document", reason=None)

        if mime in {"application/octet-stream", "binary/octet-stream"}:
            try:
                path = urlsplit(final_url).path if final_url else ""
            except ValueError:
                path = ""
            suffix = PurePosixPath(path).suffix.lower()
            group = EXTENSION_GROUPS.get(suffix)
            if group is not None:
                return AssetResponseDecision(persist=True, group=group, reason=None)
            return AssetResponseDecision(persist=False, group=None, reason="unsupported_asset_type")

        return AssetResponseDecision(persist=False, group=None, reason="unsupported_asset_type")
