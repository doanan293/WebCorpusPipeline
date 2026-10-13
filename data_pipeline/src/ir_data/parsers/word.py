from __future__ import annotations

from docx import Document
from docx.document import Document as DocumentType
from docx.table import Table
from docx.text.paragraph import Paragraph
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P

from ir_data.models import DownloadedFile, ExtractedPart, LocalResult
from ir_data.parsers.common import result, useful_text


def _blocks(parent) -> list[Paragraph | Table]:
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


def parse_docx(file: DownloadedFile) -> tuple[str, bool]:
    document = Document(file.path)
    chunks: list[str] = []
    for item in _blocks(document):
        text = item.text if isinstance(item, Paragraph) else _table_text(item)
        if text.strip():
            chunks.append(text.strip())
    text = "\n".join(chunks).strip()
    return text, useful_text(text)


def parse_word(file: DownloadedFile) -> LocalResult:
    text, useful = parse_docx(file)
    return result(
        file, [ExtractedPart(1, "document", text, not useful, "python-docx")], "python-docx"
    )
