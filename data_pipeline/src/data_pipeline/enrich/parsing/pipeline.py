from __future__ import annotations

import tempfile
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from pathlib import Path

from data_pipeline.core.settings import Settings
from data_pipeline.enrich.models import DownloadedFile, LocalResult, ParseResult, RunSummary
from data_pipeline.enrich.parsing.cache import cache_key, read_cached, write_cached
from data_pipeline.enrich.parsing.cloud.key_pool import KeyPool
from data_pipeline.enrich.parsing.cloud.llamaparse import merge_parts, parse_cloud_subset
from data_pipeline.enrich.parsing.local import parse_local
from data_pipeline.enrich.parsing.local.pdf import build_pdf_subset, cloud_part_indices
from data_pipeline.enrich.state import WorkState

# Part of the LlamaParse cache key. Bump it when the way cloud results are read changes, so stale
# cached results are not reused ("page-text": text is read per page from the "text" expansion).
CLOUD_CACHE_REVISION = "fast/latest/page-text"


def extract_documents(
    state: WorkState, settings: Settings, *, api_keys: list[tuple[str, str]]
) -> RunSummary:
    keys = KeyPool(api_keys)
    cache_root = state.root / "cache"
    pending = list(state.pending_parse())

    def parse_local_safe(
        file: DownloadedFile,
    ) -> tuple[DownloadedFile, LocalResult | None, Exception | None]:
        try:
            return file, parse_local(file), None
        except Exception as exc:  # noqa: BLE001 -- a parser crash is recorded as that file's failure
            return file, None, exc

    def parse_cloud(file: DownloadedFile, local: LocalResult, missing: list[int]) -> ParseResult:
        with tempfile.TemporaryDirectory(prefix="ir-parse-") as temp:
            subset = Path(temp) / "subset.pdf"
            mapping = build_pdf_subset(file.path, missing, subset)
            return parse_cloud_subset(subset, local, mapping, state, settings, keys)

    def finish_cloud(
        file: DownloadedFile, local: LocalResult, key: str, remote: ParseResult
    ) -> None:
        if remote.status == "extracted":
            write_cached(cache_root, key, remote)
        state.mark_parse(file.url, merge_parts(local, remote), cache_root / f"{key}.json")

    # LlamaParse jobs mostly wait on the server, so up to cloud_workers of them run at once while
    # local parsing continues; results are written back on this thread.
    in_cloud: dict[Future[ParseResult], tuple[DownloadedFile, LocalResult, str]] = {}
    with (
        ThreadPoolExecutor(max_workers=settings.local_workers) as workers,
        ThreadPoolExecutor(max_workers=settings.cloud_workers) as cloud,
    ):
        for file, local, error in workers.map(parse_local_safe, pending):
            if local is None:
                state.record_failure(file.url, "parse", str(error))
                failed_versions: dict[str, str] = {}
                state.mark_parse(
                    file.url,
                    ParseResult(file.sha256, [], "failed", [str(error)], failed_versions),
                    None,
                )
                continue
            missing: list[int] = (
                cloud_part_indices(local) if file.path.suffix.lower() == ".pdf" else []
            )
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
            key = cache_key(file.sha256, missing, local.parser_version, CLOUD_CACHE_REVISION)
            remote = read_cached(cache_root, key)
            if remote is None:
                in_cloud[cloud.submit(parse_cloud, file, local, missing)] = (file, local, key)
            else:
                finish_cloud(file, local, key, remote)
        for future in as_completed(in_cloud):
            finish_cloud(*in_cloud[future], future.result())
    return state.summary()
