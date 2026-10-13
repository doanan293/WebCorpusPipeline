from __future__ import annotations

from pathlib import Path

from ir_data.models import DownloadedFile
from ir_data.parsers.excel import parse_xls, parse_xlsx
from ir_data.parsers.office import convert_office
from ir_data.parsers.pdf import parse_pdf
from ir_data.parsers.powerpoint import parse_powerpoint
from ir_data.parsers.word import parse_word


def parse_local(file: DownloadedFile):
    ext = Path(file.path).suffix.lower()
    if ext == ".pdf":
        return parse_pdf(file)
    if ext == ".docx":
        return parse_word(file)
    if ext == ".xlsx":
        return parse_xlsx(file)
    if ext == ".xls":
        return parse_xls(file)
    if ext == ".pptx":
        return parse_powerpoint(file)
    if ext in {".doc", ".ppt", ".odt", ".ods", ".odp"}:
        target = (
            "docx:Office Open XML Text"
            if ext in {".doc", ".odt"}
            else "pptx:Office Open XML Presentation"
            if ext in {".ppt", ".odp"}
            else "xlsx:Office Open XML Spreadsheet"
        )
        with __import__("tempfile").TemporaryDirectory(prefix="ir-convert-") as temp:
            converted = convert_office(file.path, Path(temp), target)
            derived = DownloadedFile(
                file.url,
                converted,
                file.sha256,
                converted.stat().st_size,
                file.mime_type,
                file.final_url,
            )
            if ext in {".doc", ".odt"}:
                return parse_word(derived)
            if ext in {".ppt", ".odp"}:
                return parse_powerpoint(derived)
            return parse_xlsx(derived)
    if ext in {".txt", ".csv", ".rtf"}:
        text = file.path.read_text(errors="replace")
        from ir_data.models import ExtractedPart
        from ir_data.parsers.common import result

        return result(file, [ExtractedPart(1, "document", text, False, "text")], "text")
    raise ValueError(f"unsupported document extension: {ext}")
