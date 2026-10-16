from __future__ import annotations

from data_pipeline.enrich.domain.models import LocalResult, ParseResult


def merge_parts(local: LocalResult, remote: ParseResult) -> ParseResult:
    by_index = {p.index: p for p in local.parts}
    for part in remote.parts:
        if part.text.strip():
            by_index[part.index] = part
    return ParseResult(
        local.source_sha256,
        [by_index[i] for i in sorted(by_index)],
        remote.status,
        local.warnings + remote.warnings,
        {"local": local.parser_version, **remote.parser_versions},
    )
