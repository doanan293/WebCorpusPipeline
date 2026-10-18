from __future__ import annotations

from typing import Any

from data_pipeline.domain.parser.models import ExtractedPart, ParseResult


def parse_result_from_dict(value: dict[str, Any]) -> ParseResult:
    return ParseResult(
        value["source_sha256"],
        [ExtractedPart(**p) for p in value.get("parts", [])],
        value["status"],
        value.get("warnings", []),
        value.get("parser_versions", {}),
    )
