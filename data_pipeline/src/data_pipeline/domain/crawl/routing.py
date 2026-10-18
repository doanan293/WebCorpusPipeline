from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus
from typing import Literal, cast

from data_pipeline.domain.parser.extract.links import resource_kind

ResponsePurpose = Literal["page", "robots", "sitemap", "asset"]
RouteAction = Literal[
    "html",
    "robots",
    "sitemap",
    "file",
    "transient",
    "unknown",
    "optional_absent",
    "http_error",
]


@dataclass(frozen=True, slots=True)
class ResponseRoute:
    action: RouteAction
    content_type: str
    error: str | None = None


def route_response(
    *, url: str, status: int, content_type: str, purpose: ResponsePurpose
) -> ResponseRoute:
    normalized_type = content_type.partition(";")[0].strip().lower()
    if status >= HTTPStatus.BAD_REQUEST:
        if purpose in {"robots", "sitemap"} and status in {404, 410}:
            return ResponseRoute("optional_absent", normalized_type)
        return ResponseRoute("http_error", normalized_type, f"http_{status}")
    if purpose in {"robots", "sitemap"}:
        return ResponseRoute(purpose, normalized_type)
    if purpose == "asset":
        return ResponseRoute("file", normalized_type)
    return ResponseRoute(cast(RouteAction, resource_kind(url, normalized_type)), normalized_type)
