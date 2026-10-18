from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from data_pipeline.application.parser.ports import (
    AggregatorClient,
    CloudParser,
    LocalParser,
    ParseCache,
)
from data_pipeline.domain.parser.cache_key import CLOUD_CACHE_REVISION, cache_key
from data_pipeline.domain.parser.cloud_pages import cloud_part_indices
from data_pipeline.domain.parser.extract.article import analyze_article
from data_pipeline.domain.parser.key_pool import KeysExhaustedError
from data_pipeline.domain.parser.merge import merge_parts
from data_pipeline.domain.parser.models import DownloadedFile, ExtractedPart, LocalResult
from data_pipeline.domain.parser.records import document_format, html_record, other_record

log = logging.getLogger(__name__)

type ParseOutcome = Literal["sent", "unparsable", "unsent"]


def _charset(content_type: str) -> str | None:
    for part in content_type.split(";")[1:]:
        key, _, value = part.strip().partition("=")
        if key.lower() == "charset" and value:
            return value.strip("\"'")
    return None


@dataclass(frozen=True, slots=True)
class DocumentReaders:
    """What reading a document needs: the local parsers, LlamaParse (or ``None``), its cache."""

    local: LocalParser
    cloud: CloudParser | None
    cache: ParseCache
    scratch: Path


async def parse_payload(
    url: str,
    fmt: str,
    content_type: str,
    body: bytes,
    *,
    readers: DocumentReaders,
    aggregator: AggregatorClient,
) -> ParseOutcome:
    """Extract one downloaded page or document and send the record to the aggregator."""
    if fmt == "html":
        analysis = await asyncio.to_thread(analyze_article, body, url, _charset(content_type))
        record = html_record(url, analysis)
    else:
        doc_format = document_format(url, content_type)
        try:
            text = await asyncio.to_thread(
                _document_text, url, doc_format, content_type, body, readers
            )
        except Exception as exc:  # noqa: BLE001 -- a broken document is logged, never fatal
            log.warning("cannot parse %s: %s", url, exc)
            return "unparsable"
        record = other_record(url, doc_format, text)
    return "sent" if await aggregator.send(record) else "unsent"


def _document_text(
    url: str,
    doc_format: str,
    content_type: str,
    body: bytes,
    readers: DocumentReaders,
) -> str:
    readers.scratch.mkdir(parents=True, exist_ok=True)
    path = readers.scratch / f"{uuid.uuid4().hex}.{doc_format}"
    path.write_bytes(body)
    try:
        sha256 = hashlib.sha256(body).hexdigest()
        file = DownloadedFile(url, path, sha256, len(body), content_type, url)
        result = readers.local.parse(file)
        parts = _with_cloud(url, file, result, readers.cloud, readers.cache)
        return "\n\n".join(part.text for part in parts if part.text.strip())
    finally:
        path.unlink(missing_ok=True)


def _with_cloud(
    url: str,
    file: DownloadedFile,
    result: LocalResult,
    cloud: CloudParser | None,
    cache: ParseCache,
) -> list[ExtractedPart]:
    """Local parts, with pages that need OCR filled from the cache or LlamaParse when possible."""
    missing = cloud_part_indices(result)
    if not missing:
        return result.parts
    key = cache_key(file.sha256, missing, result.parser_version, CLOUD_CACHE_REVISION)
    remote = cache.read(key)
    if remote is None and cloud is not None:
        try:
            remote = cloud.parse(url, file, result, missing)
        except KeysExhaustedError:
            log.warning("LlamaParse quota exhausted; %s keeps its local text", url)
            remote = None
        if remote is not None and remote.status == "extracted":
            cache.write(key, remote)
    if remote is None or remote.status != "extracted":
        return result.parts
    return merge_parts(result, remote).parts
