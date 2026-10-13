from __future__ import annotations

from pathlib import Path

from data_pipeline.enrich.models import DownloadedFile
from data_pipeline.enrich.parsing.local.excel import parse_xls, parse_xlsx
from data_pipeline.enrich.parsing.local.office import convert_office
from data_pipeline.enrich.parsing.local.pdf import parse_pdf
from data_pipeline.enrich.parsing.local.powerpoint import parse_powerpoint
from data_pipeline.enrich.parsing.local.word import parse_word


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
        from data_pipeline.enrich.models import ExtractedPart
        from data_pipeline.enrich.parsing.local.common import result

        return result(
            file,
            [ExtractedPart(1, "document", text, has_visual_content=False, parser="text")],
            "text",
        )
    msg = f"unsupported document extension: {ext}"
    raise ValueError(msg)
