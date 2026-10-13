from __future__ import annotations

import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from data_pipeline.enrich.models import ParseResult, RunSummary
from data_pipeline.enrich.parsing.cache import cache_key, read_cached, write_cached
from data_pipeline.enrich.parsing.cloud.key_pool import KeyPool
from data_pipeline.enrich.parsing.cloud.llamaparse import merge_parts, parse_cloud_subset
from data_pipeline.enrich.parsing.local import parse_local
from data_pipeline.enrich.parsing.local.pdf import build_pdf_subset, cloud_part_indices
from data_pipeline.enrich.state import WorkState


def extract_documents(state: WorkState, settings, *, api_keys: list[tuple[str, str]]) -> RunSummary:
    keys = KeyPool(api_keys)
    cache_root = state.root / "cache"
    pending = list(state.pending_parse())

    def parse_local_safe(file):
        try:
            return file, parse_local(file), None
        except Exception as exc:
            return file, None, exc

    with ThreadPoolExecutor(max_workers=settings.local_workers) as workers:
        for file, local, error in workers.map(parse_local_safe, pending):
            if error is not None:
                state.record_failure(file.url, "parse", str(error))
                state.mark_parse(
                    file.url, ParseResult(file.sha256, [], "failed", [str(error)], {}), None
                )
                continue
            missing = cloud_part_indices(local) if file.path.suffix.lower() == ".pdf" else []
            if not missing:
                state.mark_parse(
                    file.url,
                    ParseResult(
                        local.source_sha256,
                        local.parts,
                        "extracted",
                        local.warnings,
                        {"local": local.parser_version},
                    ),
                    None,
                )
                continue
            key = cache_key(file.sha256, missing, local.parser_version, "fast/latest")
            remote = read_cached(cache_root, key)
            if remote is None:
                with tempfile.TemporaryDirectory(prefix="ir-parse-") as temp:
                    subset = Path(temp) / "subset.pdf"
                    mapping = build_pdf_subset(file.path, missing, subset)
                    remote = parse_cloud_subset(subset, local, mapping, state, settings, keys)
                if remote.status in {"extracted", "cloud_failed"}:
                    write_cached(cache_root, key, remote)
            merged = merge_parts(local, remote)
            state.mark_parse(file.url, merged, cache_root / f"{key}.json")
    return state.summary()
