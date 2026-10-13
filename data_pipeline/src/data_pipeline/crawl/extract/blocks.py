from __future__ import annotations

from copy import deepcopy
from typing import Any

from lxml import etree

from data_pipeline.crawl.extract.links import normalized_text


def blocks_from_article(element: etree._Element) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    heading_stack: dict[int, str] = {}

    def current_heading_path() -> list[str]:
        return [heading_stack[lvl] for lvl in sorted(heading_stack.keys())]

    def walk(node: etree._Element) -> None:
        if not isinstance(node.tag, str):
            return
        tag = node.tag.lower()

        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            level = int(tag[1])
            text = normalized_text(list(node.itertext()))
            if text:
                for lvl in list(heading_stack.keys()):
                    if lvl >= level:
                        del heading_stack[lvl]
                heading_stack[level] = text
                blocks.append(
                    {
                        "type": "heading",
                        "level": level,
                        "text": text,
                        "heading_path": current_heading_path(),
                    }
                )
        elif tag == "p":
            text = normalized_text(list(node.itertext()))
            if text:
                blocks.append(
                    {
                        "type": "paragraph",
                        "text": text,
                        "heading_path": current_heading_path(),
                    }
                )
        elif tag in {"ul", "ol"}:
            items: list[str] = []
            for li in node.xpath(".//li"):
                li_copy = deepcopy(li)
                for sublist in li_copy.xpath(".//ul | .//ol"):
                    sublist.drop_tree()
                item_text = normalized_text(list(li_copy.itertext()))
                if item_text:
                    items.append(item_text)
            if items:
                blocks.append(
                    {
                        "type": "list",
                        "ordered": tag == "ol",
                        "items": items,
                        "heading_path": current_heading_path(),
                    }
                )
        elif tag == "blockquote":
            text = normalized_text(list(node.itertext()))
            if text:
                blocks.append(
                    {
                        "type": "quote",
                        "text": text,
                        "heading_path": current_heading_path(),
                    }
                )
        elif tag == "pre":
            code_el = node.find(".//code")
            target = code_el if code_el is not None else node
            code_text = "".join(target.itertext()).strip("\r\n")
            if code_text:
                blocks.append(
                    {
                        "type": "code",
                        "text": code_text,
                        "heading_path": current_heading_path(),
                    }
                )
        elif tag == "table":
            headers: list[list[str]] = []
            rows: list[list[str]] = []
            thead_trs = node.xpath(".//thead/tr")
            if thead_trs:
                for tr in thead_trs:
                    row = [normalized_text(list(c.itertext())) for c in tr.xpath("./th | ./td")]
                    if any(row):
                        headers.append(row)
                for tr in node.xpath(".//tbody/tr | ./tr"):
                    row = [normalized_text(list(c.itertext())) for c in tr.xpath("./th | ./td")]
                    if any(row):
                        rows.append(row)
            else:
                for tr in node.xpath(".//tbody/tr | ./tr"):
                    ths = tr.xpath("./th")
                    row = [normalized_text(list(c.itertext())) for c in tr.xpath("./th | ./td")]
                    if not any(row):
                        continue
                    if ths and not headers and not rows:
                        headers.append(row)
                    else:
                        rows.append(row)
            if headers or rows:
                blocks.append(
                    {
                        "type": "table",
                        "headers": headers,
                        "rows": rows,
                        "heading_path": current_heading_path(),
                    }
                )
        elif tag == "figure":
            img = node.find(".//img")
            caption_el = node.find(".//figcaption")
            asset_url = img.get("src", "") if img is not None else ""
            alt = img.get("alt", "") if img is not None else ""
            caption = normalized_text(list(caption_el.itertext())) if caption_el is not None else ""
            blocks.append(
                {
                    "type": "figure",
                    "asset_url": asset_url,
                    "alt": alt,
                    "caption": caption,
                    "heading_path": current_heading_path(),
                }
            )
        elif tag == "img":
            blocks.append(
                {
                    "type": "figure",
                    "asset_url": node.get("src", ""),
                    "alt": node.get("alt", ""),
                    "caption": "",
                    "heading_path": current_heading_path(),
                }
            )
        elif tag == "hr":
            blocks.append(
                {
                    "type": "separator",
                    "heading_path": current_heading_path(),
                }
            )
        else:
            if node.text and node.text.strip():
                t = normalized_text([node.text])
                if t:
                    blocks.append(
                        {
                            "type": "paragraph",
                            "text": t,
                            "heading_path": current_heading_path(),
                        }
                    )
            for child in node:
                walk(child)
                if child.tail and child.tail.strip():
                    t = normalized_text([child.tail])
                    if t:
                        blocks.append(
                            {
                                "type": "paragraph",
                                "text": t,
                                "heading_path": current_heading_path(),
                            }
                        )

    walk(element)
    return blocks


def text_from_blocks(blocks: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for block in blocks:
        btype = block.get("type")
        if btype in {"heading", "paragraph", "quote"}:
            t = block.get("text")
            if t and isinstance(t, str):
                lines.append(t)
        elif btype == "list":
            for item in block.get("items", []):
                if item and isinstance(item, str):
                    lines.append(item)
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
