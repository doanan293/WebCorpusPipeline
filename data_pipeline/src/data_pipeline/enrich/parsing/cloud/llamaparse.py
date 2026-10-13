from __future__ import annotations

import uuid
from pathlib import Path

from data_pipeline.enrich.models import ExtractedPart, LocalResult, ParseResult
from data_pipeline.enrich.parsing.cloud.errors import classify_cloud_error
from data_pipeline.enrich.parsing.cloud.key_pool import KeyPool


def _value(obj, name, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _page_texts(response) -> list[str]:
    raw = _value(response, "pages") or _value(response, "items") or []
    values = []
    if isinstance(raw, dict):
        raw = list(raw.values())
    for item in raw:
        text = _value(item, "text") or _value(item, "markdown") or _value(item, "text_full") or ""
        values.append(str(text))
    if not values:
        text = _value(response, "text_full") or _value(response, "markdown_full") or ""
        values = [str(text)] if text else []
    return values


def parse_cloud_subset(
    file: Path, local: LocalResult, mapping: dict[int, int], state, settings, pool: KeyPool
) -> ParseResult:
    try:
        from llama_cloud import LlamaCloud
    except ImportError as exc:
        raise RuntimeError("install the producer extra to use LlamaParse") from exc
    pending = list(mapping.items())
    label, secret = pool.current()
    request_id = uuid.uuid4().hex
    try:
        client = LlamaCloud(api_key=secret)
        uploaded = client.files.create(file=str(file), purpose="parse")
        file_id = str(_value(uploaded, "id"))
        state.save_cloud_job(
            request_id,
            str(file),
            file_id=file_id,
            key_label=label,
            key_fingerprint=pool.fingerprint(secret),
            status="uploaded",
        )
        job = client.parsing.create(file_id=file_id, tier="fast", version="latest")
        job_id = str(_value(job, "id") or _value(job, "job_id"))
        state.save_cloud_job(
            request_id,
            str(file),
            file_id=file_id,
            job_id=job_id,
            key_label=label,
            key_fingerprint=pool.fingerprint(secret),
            status="accepted",
        )
        client.parsing.wait_for_completion(
            job_id,
            polling_interval=settings.cloud_poll_seconds,
            timeout=settings.cloud_timeout_seconds,
        )
        response = client.parsing.get(job_id, expand=["markdown_full", "text_full", "items"])
        texts = _page_texts(response)
        parts = []
        for remote_index, original_index in pending:
            text = texts[remote_index - 1] if remote_index <= len(texts) else ""
            parts.append(ExtractedPart(original_index, "page", text, bool(text), "llamaparse-fast"))
        state.save_cloud_job(
            request_id,
            str(file),
            file_id=file_id,
            job_id=job_id,
            key_label=label,
            key_fingerprint=pool.fingerprint(secret),
            status="completed",
        )
        return ParseResult(
            local.source_sha256, parts, "extracted", [], {"llamaparse": "fast/latest"}
        )
    except Exception as exc:
        status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
        kind = classify_cloud_error(status, exc)
        if kind == "quota":
            pool.mark_quota_exhausted(label)
        state.save_cloud_job(
            request_id,
            str(file),
            key_label=label,
            key_fingerprint=pool.fingerprint(secret),
            status=kind,
            payload=str(exc)[:500],
        )
        if kind == "quota" and len(pool.exhausted) < len(pool.keys):
            return parse_cloud_subset(file, local, mapping, state, settings, pool)
        return ParseResult(
            local.source_sha256,
            [],
            "quota_blocked" if kind == "quota" else "cloud_failed",
            [f"{kind}: {exc}"],
            {},
        )


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
