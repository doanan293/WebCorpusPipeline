from __future__ import annotations

import asyncio
import hashlib
import io
import threading
import time
from pathlib import Path

import docx
import pymupdf

from crawler.application.reader.parse import DocumentReaders, parse_payload
from crawler.domain.reader.cache_key import CLOUD_CACHE_REVISION, cache_key
from crawler.domain.reader.key_pool import KeysExhaustedError
from crawler.domain.reader.models import (
    DownloadedFile,
    ExtractedPart,
    LocalResult,
    ParseResult,
)
from crawler.infra.reader.readers.dispatch import LocalReaderAdapter
from tests.fakes.reader import FakeCloud, FakeMergerClient, MemoryParseCache


def _docx() -> bytes:
    document = docx.Document()
    document.add_paragraph("Quyet dinh tuyen sinh 2026")
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _scanned_pdf() -> bytes:
    document = pymupdf.open()
    page = document.new_page()
    page.draw_rect(pymupdf.Rect(50, 50, 300, 300), fill=(0, 0, 0))
    return document.tobytes()


def _parse(
    fmt: str,
    url: str,
    body: bytes,
    tmp_path: Path,
    *,
    cloud: FakeCloud | None = None,
    cache: MemoryParseCache | None = None,
    content_type: str = "",
) -> tuple[str, FakeMergerClient]:
    merger = FakeMergerClient()
    outcome = asyncio.run(
        parse_payload(
            url,
            fmt,
            content_type,
            body,
            readers=DocumentReaders(
                LocalReaderAdapter(), cloud, cache or MemoryParseCache(), tmp_path
            ),
            merger=merger,
        )
    )
    return outcome, merger


def test_parse_html_sends_html_record(tmp_path: Path) -> None:
    body = "<html><head><title>Tin</title></head><body><article><p>Đại học Bách khoa</p></article></body></html>"
    outcome, merger = _parse(
        "html",
        "https://h/a",
        body.encode("utf-8"),
        tmp_path,
        content_type="text/html; charset=utf-8",
    )
    assert outcome == "sent"
    assert merger.records[0]["format"] == "html"
    assert "Đại học Bách khoa" in merger.records[0]["content"]


def test_parse_docx_sends_other_record(tmp_path: Path) -> None:
    outcome, merger = _parse("other", "https://h/qd.docx", _docx(), tmp_path)
    assert outcome == "sent"
    (record,) = merger.records
    assert record["format"] == "docx"
    assert "Quyet dinh tuyen sinh 2026" in record["content"]
    assert list(tmp_path.iterdir()) == []


def test_corrupt_pdf_is_unparsable(tmp_path: Path) -> None:
    outcome, merger = _parse("other", "https://h/x.pdf", b"%PDF-broken", tmp_path)
    assert outcome == "unparsable"
    assert merger.records == []


def _must_not_run(_file: DownloadedFile, _local: LocalResult, _missing: list[int]) -> ParseResult:
    raise AssertionError


def test_cloud_cache_hit_skips_cloud(tmp_path: Path) -> None:
    body = _scanned_pdf()
    local = LocalReaderAdapter()
    probe = tmp_path / "probe.pdf"
    probe.write_bytes(body)
    sha = hashlib.sha256(body).hexdigest()
    first = local.parse(DownloadedFile("u", probe, sha, len(body), "application/pdf", "u"))
    probe.unlink()
    cache = MemoryParseCache()
    key = cache_key(sha, [1], first.reader_version, CLOUD_CACHE_REVISION)
    cache.results[key] = ParseResult(
        sha, [ExtractedPart(1, "page", "Chu tu LlamaParse")], "extracted"
    )
    cloud = FakeCloud(_must_not_run)
    outcome, merger = _parse(
        "other", "https://h/scan.pdf", body, tmp_path, cloud=cloud, cache=cache
    )
    assert outcome == "sent"
    assert "Chu tu LlamaParse" in merger.records[0]["content"]
    assert cloud.calls == []


def test_cloud_quota_exhausted_falls_back_to_local(tmp_path: Path) -> None:
    def exhausted(*_: object) -> ParseResult:
        raise KeysExhaustedError

    outcome, merger = _parse(
        "other", "https://h/scan.pdf", _scanned_pdf(), tmp_path, cloud=FakeCloud(exhausted)
    )
    assert outcome == "sent"
    assert merger.records[0]["format"] == "pdf"


def test_merger_down_is_unsent(tmp_path: Path) -> None:
    merger = FakeMergerClient(ok=False)
    readers = DocumentReaders(LocalReaderAdapter(), None, MemoryParseCache(), tmp_path)
    outcome = asyncio.run(
        parse_payload("https://h/qd.docx", "other", "", _docx(), readers=readers, merger=merger)
    )
    assert outcome == "unsent"


class _SlowLocal:
    """A local reader that records how many documents it reads at once."""

    def __init__(self) -> None:
        self.active = 0
        self.most = 0
        self.lock = threading.Lock()

    def parse(self, file: DownloadedFile) -> LocalResult:
        with self.lock:
            self.active += 1
            self.most = max(self.most, self.active)
        time.sleep(0.05)
        with self.lock:
            self.active -= 1
        return LocalResult(file.sha256, [ExtractedPart(0, "page", "text")], reader_version="fake-1")


def test_document_parsing_respects_worker_limit(tmp_path: Path) -> None:
    local = _SlowLocal()
    readers = DocumentReaders(local, None, MemoryParseCache(), tmp_path, limit=asyncio.Semaphore(2))

    async def scenario() -> None:
        await asyncio.gather(
            *(
                parse_payload(
                    f"https://h/{i}.docx",
                    "other",
                    "",
                    b"x",
                    readers=readers,
                    merger=FakeMergerClient(),
                )
                for i in range(6)
            )
        )

    asyncio.run(scenario())
    assert local.most == 2
