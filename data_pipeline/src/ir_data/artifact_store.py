from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path

from .models import DownloadedFile


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
    if suffix and len(suffix) <= 10:
        return suffix
    return mimetypes.guess_extension(mime.split(";", 1)[0].strip()) or ".bin"


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
