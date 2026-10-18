from __future__ import annotations

from typing import Any

from docx import Document
from docx.document import Document as DocumentType
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table
from docx.text.paragraph import Paragraph

from data_pipeline.domain.parser.models import DownloadedFile, ExtractedPart, LocalResult
from data_pipeline.infrastructure.parser.parsers.common import result


def _blocks(parent: Any) -> list[Paragraph | Table]:
    element = parent.element.body if isinstance(parent, DocumentType) else parent._tc
    return [
        Paragraph(child, parent) if isinstance(child, CT_P) else Table(child, parent)
        for child in element.iterchildren()
        if isinstance(child, (CT_P, CT_Tbl))
    ]


def _table_text(table: Table) -> str:
    return "\n".join(
        "\t".join((cell.text or "").strip() for cell in row.cells) for row in table.rows
    )


def parse_word(file: DownloadedFile) -> LocalResult:
    document = Document(str(file.path))
    chunks: list[str] = []
    for item in _blocks(document):
        text = item.text if isinstance(item, Paragraph) else _table_text(item)
        if text.strip():
            chunks.append(text.strip())
    # Visual content means the document contains images or drawings, as for every parser.
    visual = bool(document.element.body.xpath(".//w:drawing | .//w:pict"))
    part = ExtractedPart(1, "document", "\n".join(chunks).strip(), visual, "python-docx")
    return result(file, [part], "python-docx")
