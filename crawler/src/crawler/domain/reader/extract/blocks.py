from __future__ import annotations

from copy import deepcopy
from typing import Any

from lxml import etree

from crawler.domain.reader.extract.links import normalized_text

_HEADINGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
_INLINE_TAGS = frozenset(
    {"a", "b", "strong", "em", "i", "span", "u", "small", "sup", "sub", "mark", "code", "br"}
)


def _text(node: etree._Element) -> str:
    return normalized_text(list(node.itertext()))


def _inline_source(node: etree._Element) -> str:
    """Concatenate an element's text as it reads, with ``<br>`` as a line break."""
    if node.tag.lower() == "br":
        return "\n"
    parts = [node.text or ""]
    for child in node:
        if isinstance(child.tag, str):
            parts.append(_inline_source(child))
        parts.append(child.tail or "")
    return "".join(parts)


def _lines(source: str) -> str:
    """Collapse whitespace within each line and drop empty lines."""
    lines = (" ".join(line.split()) for line in source.split("\n"))
    return "\n".join(line for line in lines if line)


def _row_cells(tr: etree._Element) -> list[str]:
    return [_text(cell) for cell in tr.xpath("./th | ./td")]


class _BlockCollector:
    """Walks a sanitized article tree and records its structural blocks."""

    def __init__(self) -> None:
        self.blocks: list[dict[str, Any]] = []
        self._heading_stack: dict[int, str] = {}

    def _add(self, **block: Any) -> None:
        path = [self._heading_stack[level] for level in sorted(self._heading_stack)]
        self.blocks.append({**block, "heading_path": path})

    def walk(self, node: etree._Element) -> None:
        if not isinstance(node.tag, str):
            return
        tag = node.tag.lower()
        if tag in _HEADINGS:
            self._heading(node, int(tag[1]))
        elif tag == "p":
            self._paragraph_from(_inline_source(node))
        elif tag in {"ul", "ol"}:
            self._list(node, ordered=tag == "ol")
        elif tag == "blockquote":
            self._text_block("quote", node)
        elif tag == "pre":
            self._code(node)
        elif tag == "table":
            self._table(node)
        elif tag == "figure":
            self._figure(node)
        elif tag == "img":
            self._add(
                type="figure", asset_url=node.get("src", ""), alt=node.get("alt", ""), caption=""
            )
        elif tag == "hr":
            self._add(type="separator")
        else:
            self._container(node)

    def _heading(self, node: etree._Element, level: int) -> None:
        text = _text(node)
        if not text:
            return
        for known in list(self._heading_stack):
            if known >= level:
                del self._heading_stack[known]
        self._heading_stack[level] = text
        self._add(type="heading", level=level, text=text)

    def _text_block(self, kind: str, node: etree._Element) -> None:
        text = _text(node)
        if text:
            self._add(type=kind, text=text)

    def _list(self, node: etree._Element, *, ordered: bool) -> None:
        items: list[str] = []
        for li in node.xpath(".//li"):
            li_copy = deepcopy(li)
            for sublist in li_copy.xpath(".//ul | .//ol"):
                sublist.drop_tree()
            item_text = _text(li_copy)
            if item_text:
                items.append(item_text)
        if items:
            self._add(type="list", ordered=ordered, items=items)

    def _code(self, node: etree._Element) -> None:
        code_el = node.find(".//code")
        target = code_el if code_el is not None else node
        code_text = "".join(target.itertext()).strip("\r\n")
        if code_text:
            self._add(type="code", text=code_text)

    def _table(self, node: etree._Element) -> None:
        headers: list[list[str]] = []
        rows: list[list[str]] = []
        body_rows = node.xpath(".//tbody/tr | ./tr")
        thead_trs = node.xpath(".//thead/tr")
        if thead_trs:
            headers.extend(row for row in map(_row_cells, thead_trs) if any(row))
            rows.extend(row for row in map(_row_cells, body_rows) if any(row))
        else:
            for tr in body_rows:
                row = _row_cells(tr)
                if not any(row):
                    continue
                if tr.xpath("./th") and not headers and not rows:
                    headers.append(row)
                else:
                    rows.append(row)
        if headers or rows:
            self._add(type="table", headers=headers, rows=rows)

    def _figure(self, node: etree._Element) -> None:
        img = node.find(".//img")
        caption_el = node.find(".//figcaption")
        self._add(
            type="figure",
            asset_url=img.get("src", "") if img is not None else "",
            alt=img.get("alt", "") if img is not None else "",
            caption=_text(caption_el) if caption_el is not None else "",
        )

    def _container(self, node: etree._Element) -> None:
        # Text, tails and inline children form one paragraph until a block child ends it.
        run: list[str] = [node.text or ""]
        for child in node:
            if isinstance(child.tag, str) and child.tag.lower() in _INLINE_TAGS:
                run.append(_inline_source(child))
            elif isinstance(child.tag, str):
                self._paragraph_from("".join(run))
                run = []
                self.walk(child)
            run.append(child.tail or "")
        self._paragraph_from("".join(run))

    def _paragraph_from(self, source: str) -> None:
        text = _lines(source)
        if text:
            self._add(type="paragraph", text=text)


def blocks_from_article(element: etree._Element) -> list[dict[str, Any]]:
    collector = _BlockCollector()
    collector.walk(element)
    return collector.blocks


def text_from_blocks(blocks: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for block in blocks:
        btype = block.get("type")
        if btype in {"heading", "paragraph", "quote"}:
            t = block.get("text")
            if t and isinstance(t, str):
                lines.append(t)
        elif btype == "list":
            lines.extend(item for item in block.get("items", []) if item and isinstance(item, str))
        elif btype == "code":
            t = block.get("text")
            if t and isinstance(t, str):
                lines.append(t)
        elif btype == "table":
            for row in block.get("headers", []):
                line = " ".join(c for c in row if c)
                if line:
                    lines.append(line)
            for row in block.get("rows", []):
                line = " ".join(c for c in row if c)
                if line:
                    lines.append(line)
        elif btype == "figure":
            cap = block.get("caption") or block.get("alt")
            if cap and isinstance(cap, str):
                lines.append(cap)
    return "\n".join(lines).strip()
