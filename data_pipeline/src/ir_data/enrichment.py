from __future__ import annotations

import tempfile
from pathlib import Path

from .cloud.key_pool import KeyPool
from .cloud.llamaparse import merge_parts, parse_cloud_subset
from .models import ParseResult, RunSummary
from .parse_cache import cache_key, read_cached, write_cached
from .parsers import parse_local
from .parsers.pdf import build_pdf_subset, cloud_part_indices
from .state import WorkState


def extract_documents(state: WorkState, settings, repo_root: Path) -> RunSummary:
    keys = KeyPool(
        __import__("ir_data.settings", fromlist=["load_api_keys"]).load_api_keys(repo_root)
    )
    cache_root = state.root / "cache"
    for file in list(state.pending_parse()):
        try:
            local = parse_local(file)
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
                    subset = tempfile.NamedTemporaryFile(suffix=".pdf", dir=temp, delete=False)
                    subset.close()
                    mapping = build_pdf_subset(file.path, missing, Path(subset.name))
                    remote = parse_cloud_subset(
                        Path(subset.name), local, mapping, state, settings, keys
                    )
                if remote.status in {"extracted", "cloud_failed"}:
                    write_cached(cache_root, key, remote)
            merged = merge_parts(local, remote)
            state.mark_parse(file.url, merged, cache_root / f"{key}.json")
        except Exception as exc:
            state.record_failure(file.url, "parse", str(exc))
            state.mark_parse(file.url, ParseResult(file.sha256, [], "failed", [str(exc)], {}), None)
    return state.summary()


def extract_documents_sync(state, settings, repo_root):
    return extract_documents(state, settings, repo_root)
