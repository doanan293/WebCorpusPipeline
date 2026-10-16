from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from data_pipeline.application.enrich.ports import WorkState
from data_pipeline.domain.enrich.models import jsonable


def export_enrichment(state: WorkState, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    documents = destination / "documents.jsonl"
    with documents.open("w", encoding="utf-8") as out:
        for row in state.inventory_rows():
            if row["status"] != "downloaded":
                continue
            parsed = state.parse_record(row["url"])
            refs = state.refs_for(row["url"])
            parts: list[Any] = jsonable(parsed.parts) if parsed else []
            versions: dict[str, str] = parsed.parser_versions if parsed else {}
            record = {
                "url": row["url"],
                "final_url": row["final_url"],
                "sha256": row["sha256"],
                "mime_type": row["mime_type"],
                "size": row["size"],
                "extraction_status": parsed.status if parsed else "pending",
                "parts": parts,
                "warnings": parsed.warnings if parsed else ["not parsed"],
                "parser_versions": versions,
                "referrers": refs,
            }
            out.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    summary = state.summary()
    report = jsonable(summary)
    (destination / "extraction-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    return destination
