from __future__ import annotations

import json
from pathlib import Path

from ir_data.models import jsonable


def export_enrichment(state, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    documents = destination / "documents.jsonl"
    with documents.open("w", encoding="utf-8") as out:
        for row in state.inventory_rows():
            if row["status"] != "downloaded":
                continue
            parsed = state.parse_record(row["url"])
            refs = state.refs_for(row["url"])
            record = {
                "url": row["url"],
                "final_url": row["final_url"],
                "sha256": row["sha256"],
                "mime_type": row["mime_type"],
                "size": row["size"],
                "extraction_status": parsed.status if parsed else "pending",
                "parts": jsonable(parsed.parts) if parsed else [],
                "warnings": parsed.warnings if parsed else ["not parsed"],
                "parser_versions": parsed.parser_versions if parsed else {},
                "referrers": refs,
            }
            out.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    summary = state.summary()
    report = {
        "downloaded": summary.downloaded,
        "parsed": summary.parsed,
        "failed": summary.failed,
        "pending": summary.pending,
        "quota_blocked": summary.quota_blocked,
        "transferred_bytes": summary.transferred_bytes,
    }
    (destination / "extraction-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    return destination
