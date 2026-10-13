from __future__ import annotations

import tempfile
from collections.abc import Callable
from pathlib import Path

from data_pipeline.enrich.models import DownloadedFile, ExtractedPart, LocalResult
from data_pipeline.enrich.parsing.local.common import result
from data_pipeline.enrich.parsing.local.excel import parse_xls, parse_xlsx
from data_pipeline.enrich.parsing.local.office import convert_office
from data_pipeline.enrich.parsing.local.pdf import parse_pdf
from data_pipeline.enrich.parsing.local.powerpoint import parse_powerpoint
from data_pipeline.enrich.parsing.local.word import parse_word

_DIRECT_PARSERS = {
    ".pdf": parse_pdf,
    ".docx": parse_word,
    ".xlsx": parse_xlsx,
    ".xls": parse_xls,
    ".pptx": parse_powerpoint,
}
_WORD_TARGET = "docx:Office Open XML Text"
_SLIDES_TARGET = "pptx:Office Open XML Presentation"
_SHEET_TARGET = "xlsx:Office Open XML Spreadsheet"
# Legacy and OpenDocument formats are converted with LibreOffice, then parsed natively.
_CONVERTED_PARSERS = {
    ".doc": (_WORD_TARGET, parse_word),
    ".odt": (_WORD_TARGET, parse_word),
    ".ppt": (_SLIDES_TARGET, parse_powerpoint),
    ".odp": (_SLIDES_TARGET, parse_powerpoint),
    ".ods": (_SHEET_TARGET, parse_xlsx),
}
_TEXT_EXTENSIONS = frozenset({".txt", ".csv", ".rtf"})


def _parse_converted(
    file: DownloadedFile, target: str, parser: Callable[[DownloadedFile], LocalResult]
) -> LocalResult:
    with tempfile.TemporaryDirectory(prefix="ir-convert-") as temp:
        converted = convert_office(file.path, Path(temp), target)
        derived = DownloadedFile(
            file.url,
            converted,
            file.sha256,
            converted.stat().st_size,
            file.mime_type,
            file.final_url,
        )
        return parser(derived)


def _parse_text(file: DownloadedFile):
    text = file.path.read_text(errors="replace")
    part = ExtractedPart(1, "document", text, has_visual_content=False, parser="text")
    return result(file, [part], "text")


def parse_local(file: DownloadedFile):
    ext = Path(file.path).suffix.lower()
    if ext in _DIRECT_PARSERS:
        return _DIRECT_PARSERS[ext](file)
    if ext in _CONVERTED_PARSERS:
        return _parse_converted(file, *_CONVERTED_PARSERS[ext])
    if ext in _TEXT_EXTENSIONS:
        return _parse_text(file)
    msg = f"unsupported document extension: {ext}"
    raise ValueError(msg)
