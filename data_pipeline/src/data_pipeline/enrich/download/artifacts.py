from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path

from data_pipeline.enrich.models import DownloadedFile


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def verify_file(file: DownloadedFile) -> bool:
    return (
        file.path.is_file()
        and file.path.stat().st_size == file.size
        and sha256_file(file.path) == file.sha256
    )


def extension_for(mime: str, url: str) -> str:
    suffix = Path(url.split("?", 1)[0]).suffix.lower()
    known = {
        ".pdf",
        ".doc",
        ".docx",
        ".odt",
        ".xls",
        ".xlsx",
        ".ods",
        ".ppt",
        ".pptx",
        ".odp",
        ".txt",
        ".csv",
        ".rtf",
    }
    if suffix in known:
        return suffix
    normalized = mime.split(";", 1)[0].strip().lower()
    by_mime = {
        "application/pdf": ".pdf",
        "application/msword": ".doc",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        "application/vnd.oasis.opendocument.text": ".odt",
        "application/vnd.ms-excel": ".xls",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
        "application/vnd.oasis.opendocument.spreadsheet": ".ods",
        "application/vnd.ms-powerpoint": ".ppt",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
        "application/vnd.oasis.opendocument.presentation": ".odp",
        "text/plain": ".txt",
        "text/csv": ".csv",
        "application/rtf": ".rtf",
    }
    return by_mime.get(normalized) or mimetypes.guess_extension(normalized) or ".bin"


def looks_like_document(path: Path, mime: str) -> bool:
    data = path.read_bytes()[:16]
    lower = mime.lower()
    if b"<html" in data.lower() or b"<!doctype" in data.lower():
        return False
    return (
        data.startswith(b"%PDF")
        or data.startswith(b"PK")
        or data.startswith(b"\xd0\xcf\x11\xe0")
        or lower.startswith(("application/", "text/csv", "text/plain"))
    )
