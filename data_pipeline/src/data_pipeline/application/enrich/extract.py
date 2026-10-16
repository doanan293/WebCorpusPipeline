from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path

from data_pipeline.application.enrich.ports import (
    CloudParser,
    Failures,
    LocalParser,
    ParseCache,
    Parses,
)
from data_pipeline.domain.enrich.cache_key import CLOUD_CACHE_REVISION, cache_key
from data_pipeline.domain.enrich.cloud_pages import cloud_part_indices
from data_pipeline.domain.enrich.key_pool import KeysExhaustedError
from data_pipeline.domain.enrich.merge import merge_parts
from data_pipeline.domain.enrich.models import DownloadedFile, LocalResult, ParseResult
from data_pipeline.domain.shared.config import Settings


def _blocked(local: LocalResult, warning: str) -> ParseResult:
    return ParseResult(local.source_sha256, [], "quota_blocked", [warning], {})


def extract_documents(
    parses: Parses,
    failures: Failures,
    settings: Settings,
    *,
    api_keys: list[tuple[str, str]],
    local: LocalParser,
    cloud: CloudParser,
    cache: ParseCache,
) -> None:
    pending = list(parses.pending_parse())

    def parse_local_safe(
        file: DownloadedFile,
    ) -> tuple[DownloadedFile, LocalResult | None, Exception | None]:
        try:
            return file, local.parse(file), None
        except Exception as exc:  # noqa: BLE001 -- a parser crash is recorded as that file's failure
            return file, None, exc

    def parse_cloud(
        file: DownloadedFile, result: LocalResult, missing: list[int], key: str
    ) -> tuple[ParseResult, Path | None]:
        """Run one LlamaParse job; any failure becomes this file's result, never the run's."""
        try:
            remote = cloud.parse(file.url, file, result, missing)
        except KeysExhaustedError as exc:
            return _blocked(result, str(exc)), None
        except Exception as exc:  # noqa: BLE001 -- recorded as this file's cloud failure
            return ParseResult(
                result.source_sha256, [], "cloud_failed", [f"error: {exc}"], {}
            ), None
        if remote.status != "extracted":
            return remote, None
        # Written here, in the worker, so a paid result survives an interrupted run.
        return remote, cache.write(key, remote)

    def finish_cloud(
        file: DownloadedFile, result: LocalResult, remote: ParseResult, cached: Path | None
    ) -> None:
        parses.mark_parse(file.url, merge_parts(result, remote), cached, used_cloud=True)

    def drain(timeout: float | None) -> None:
        done, _ = wait(in_cloud, timeout=timeout, return_when=FIRST_COMPLETED)
        for future in done:
            key = in_cloud.pop(future)
            for file, result in waiting.pop(key):
                finish_cloud(file, result, *future.result())

    # LlamaParse jobs mostly wait on the server, so up to cloud_workers of them run at once while
    # local parsing continues; finished ones are recorded between local results. One job runs per
    # cache key: the same pages of the same file under another URL wait for its result.
    in_cloud: dict[Future[tuple[ParseResult, Path | None]], str] = {}
    waiting: dict[str, list[tuple[DownloadedFile, LocalResult]]] = {}
    with (
        ThreadPoolExecutor(max_workers=settings.local_workers) as workers,
        ThreadPoolExecutor(max_workers=settings.cloud_workers) as cloud_workers,
    ):
        for file, result, error in workers.map(parse_local_safe, pending):
            if in_cloud:
                drain(timeout=0)
            if result is None:
                failures.record_failure(file.url, "parse", str(error))
                parses.mark_parse(
                    file.url, ParseResult(file.sha256, [], "failed", [str(error)], {})
                )
                continue
            is_pdf = file.path.suffix.lower() == ".pdf"
            missing: list[int] = cloud_part_indices(result) if is_pdf else []
            if not missing:
                local_versions = {"local": result.parser_version}
                parses.mark_parse(
                    file.url,
                    ParseResult(
                        result.source_sha256,
                        result.parts,
                        "extracted",
                        result.warnings,
                        local_versions,
                    ),
                )
                continue
            key = cache_key(file.sha256, missing, result.parser_version, CLOUD_CACHE_REVISION)
            remote = cache.read(key)
            if remote is not None:
                finish_cloud(file, result, remote, cache.path_for(key))
            elif not api_keys:
                finish_cloud(file, result, _blocked(result, "no LlamaParse keys"), None)
            elif key in waiting:
                waiting[key].append((file, result))
            else:
                waiting[key] = [(file, result)]
                in_cloud[cloud_workers.submit(parse_cloud, file, result, missing, key)] = key
        while in_cloud:
            drain(timeout=None)
