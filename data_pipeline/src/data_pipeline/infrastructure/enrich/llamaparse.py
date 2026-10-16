from __future__ import annotations

import contextlib
import tempfile
import uuid
from pathlib import Path
from typing import Any

import pymupdf as fitz

from data_pipeline.application.enrich.ports import WorkState
from data_pipeline.domain.enrich.cloud_errors import classify_cloud_error
from data_pipeline.domain.enrich.key_pool import KeyPool
from data_pipeline.domain.enrich.models import (
    DownloadedFile,
    ExtractedPart,
    LocalResult,
    ParseResult,
)
from data_pipeline.domain.shared.config import Settings


def _value(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _page_texts(response: Any) -> dict[int, str]:
    """Map 1-based page numbers of the parsed subset to their text.

    Plain-text pages are preferred; markdown pages are the fallback. Failed pages are left out.
    """
    for section, field in (("text", "text"), ("markdown", "markdown")):
        pages = _value(_value(response, section), "pages") or []
        texts = {
            int(_value(page, "page_number")): str(_value(page, field) or "")
            for page in pages
            if _value(page, "success") is not False and _value(page, field)
        }
        if texts:
            return texts
    return {}


def parse_cloud_subset(
    document_url: str,
    file: Path,
    local: LocalResult,
    mapping: dict[int, int],
    state: WorkState,
    settings: Settings,
    pool: KeyPool,
) -> ParseResult:
    """Parse ``file`` (a page subset of ``document_url``) with LlamaParse.

    Only an account out of quota moves on to the next key; any other failure is this file's
    ``cloud_failed``. The uploaded file is deleted from LlamaCloud whatever happens.
    """
    from llama_cloud import LlamaCloud  # noqa: PLC0415 -- llama-cloud is an optional 'enrich' extra

    label, secret = pool.current()
    request_id = uuid.uuid4().hex

    def record(status: str, **fields: str | None) -> None:
        state.save_cloud_job(
            request_id,
            document_url,
            key_label=label,
            key_fingerprint=pool.fingerprint(secret),
            status=status,
            **fields,
        )

    client = LlamaCloud(api_key=secret)
    file_id: str | None = None
    try:
        file_id = str(_value(client.files.create(file=str(file), purpose="parse"), "id"))
        record("uploaded", file_id=file_id)
        job = client.parsing.create(file_id=file_id, tier="fast", version="latest")
        job_id = str(_value(job, "id") or _value(job, "job_id"))
        record("accepted", job_id=job_id)
        client.parsing.wait_for_completion(
            job_id,
            polling_interval=settings.cloud_poll_seconds,
            timeout=settings.cloud_timeout_seconds,
        )
        texts = _page_texts(client.parsing.get(job_id, expand=["text", "markdown"]))
        record("completed")
    except Exception as exc:  # noqa: BLE001 -- any cloud failure is classified and recorded
        status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
        kind = classify_cloud_error(status, exc)
        record(kind, payload=str(exc)[:500])
        if kind == "quota":
            pool.mark_quota_exhausted(label)
            return parse_cloud_subset(document_url, file, local, mapping, state, settings, pool)
        if kind == "invalid":
            # LlamaParse will never accept this file; keep the local text instead of retrying.
            warning = f"cloud_rejected: {kind}: {exc}"
            return ParseResult(local.source_sha256, [], "extracted", [warning], {})
        return ParseResult(local.source_sha256, [], "cloud_failed", [f"{kind}: {exc}"], {})
    finally:
        if file_id is not None:
            with contextlib.suppress(Exception):
                client.files.delete(file_id)
    # A cloud page keeps the local page's visual flag: it describes the page, not the parser.
    visual = {part.index: part.has_visual_content for part in local.parts}
    parts = [
        ExtractedPart(
            original, "page", texts.get(remote, ""), visual.get(original, False), "llamaparse-fast"
        )
        for remote, original in mapping.items()
    ]
    return ParseResult(local.source_sha256, parts, "extracted", [], {"llamaparse": "fast/latest"})


def build_pdf_subset(path: Path, indices: list[int], destination: Path) -> dict[int, int]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    mapping: dict[int, int] = {}
    with fitz.open(path) as source, fitz.open() as target:
        for new_index, original in enumerate(indices, 1):
            target.insert_pdf(source, from_page=original - 1, to_page=original - 1)
            mapping[new_index] = original
        target.save(destination)
    return mapping


class LlamaParseCloud:
    """The ``CloudParser`` port: sends the missing pages of a PDF to LlamaParse."""

    def __init__(self, state: WorkState, settings: Settings, keys: list[tuple[str, str]]) -> None:
        self.state = state
        self.settings = settings
        self.pool = KeyPool(keys)

    def parse(
        self, document_url: str, file: DownloadedFile, local: LocalResult, missing: list[int]
    ) -> ParseResult:
        with tempfile.TemporaryDirectory(prefix="ir-parse-") as temp:
            subset = Path(temp) / "subset.pdf"
            mapping = build_pdf_subset(file.path, missing, subset)
            return parse_cloud_subset(
                document_url, subset, local, mapping, self.state, self.settings, self.pool
            )
