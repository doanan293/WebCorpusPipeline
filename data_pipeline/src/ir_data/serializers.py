from .models import ExtractedPart, ParseResult


def parse_result_from_dict(value: dict) -> ParseResult:
    return ParseResult(
        value["source_sha256"],
        [ExtractedPart(**p) for p in value.get("parts", [])],
        value["status"],
        value.get("warnings", []),
        value.get("parser_versions", {}),
    )
