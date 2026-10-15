from __future__ import annotations

import tempfile
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path

from data_pipeline.core.settings import Settings
from data_pipeline.enrich.models import DownloadedFile, LocalResult, ParseResult, RunSummary
from data_pipeline.enrich.parsing.cache import cache_key, read_cached, write_cached
from data_pipeline.enrich.parsing.cloud.key_pool import KeyPool, KeysExhaustedError
from data_pipeline.enrich.parsing.cloud.llamaparse import merge_parts, parse_cloud_subset
from data_pipeline.enrich.parsing.local import parse_local
from data_pipeline.enrich.parsing.local.pdf import build_pdf_subset, cloud_part_indices
from data_pipeline.enrich.state import WorkState

# Part of the LlamaParse cache key. Bump it when the way cloud results are read changes, so stale
# cached results are not reused ("page-text": text is read per page from the "text" expansion).
CLOUD_CACHE_REVISION = "fast/latest/page-text"


def _blocked(local: LocalResult, warning: str) -> ParseResult:
    return ParseResult(local.source_sha256, [], "quota_blocked", [warning], {})


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

    def parse_cloud(
        file: DownloadedFile, local: LocalResult, missing: list[int], key: str
    ) -> tuple[ParseResult, Path | None]:
        """Run one LlamaParse job; any failure becomes this file's result, never the run's."""
        try:
            with tempfile.TemporaryDirectory(prefix="ir-parse-") as temp:
                subset = Path(temp) / "subset.pdf"
                mapping = build_pdf_subset(file.path, missing, subset)
                remote = parse_cloud_subset(file.url, subset, local, mapping, state, settings, keys)
        except KeysExhaustedError as exc:
            return _blocked(local, str(exc)), None
        except Exception as exc:  # noqa: BLE001 -- recorded as this file's cloud failure
            return ParseResult(local.source_sha256, [], "cloud_failed", [f"error: {exc}"], {}), None
        if remote.status != "extracted":
            return remote, None
        # Written here, in the worker, so a paid result survives an interrupted run.
        return remote, write_cached(cache_root, key, remote)

    def finish_cloud(
        file: DownloadedFile, local: LocalResult, remote: ParseResult, cache: Path | None
    ) -> None:
        state.mark_parse(file.url, merge_parts(local, remote), cache, used_cloud=True)

    def drain(timeout: float | None) -> None:
        done, _ = wait(in_cloud, timeout=timeout, return_when=FIRST_COMPLETED)
        for future in done:
            file, local = in_cloud.pop(future)
            finish_cloud(file, local, *future.result())

    # LlamaParse jobs mostly wait on the server, so up to cloud_workers of them run at once while
    # local parsing continues; finished ones are recorded between local results.
    in_cloud: dict[Future[tuple[ParseResult, Path | None]], tuple[DownloadedFile, LocalResult]] = {}
    with (
        ThreadPoolExecutor(max_workers=settings.local_workers) as workers,
        ThreadPoolExecutor(max_workers=settings.cloud_workers) as cloud,
    ):
        for file, local, error in workers.map(parse_local_safe, pending):
            if in_cloud:
                drain(timeout=0)
            if local is None:
                state.record_failure(file.url, "parse", str(error))
                state.mark_parse(file.url, ParseResult(file.sha256, [], "failed", [str(error)], {}))
                continue
            is_pdf = file.path.suffix.lower() == ".pdf"
            missing: list[int] = cloud_part_indices(local) if is_pdf else []
            if not missing:
                local_versions = {"local": local.parser_version}
                state.mark_parse(
                    file.url,
                    ParseResult(
                        local.source_sha256,
                        local.parts,
                        "extracted",
                        local.warnings,
                        local_versions,
                    ),
                )
                continue
            key = cache_key(file.sha256, missing, local.parser_version, CLOUD_CACHE_REVISION)
            remote = read_cached(cache_root, key)
            if remote is not None:
                finish_cloud(file, local, remote, cache_root / f"{key}.json")
            elif not keys.keys:
                finish_cloud(file, local, _blocked(local, "no LlamaParse keys"), None)
            else:
                in_cloud[cloud.submit(parse_cloud, file, local, missing, key)] = (file, local)
        while in_cloud:
            drain(timeout=None)
    return state.summary()
