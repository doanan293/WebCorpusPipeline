from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urldefrag


def _url(value):
    return urldefrag(str(value or "").strip())[0]


def build_graph(source: Path, enrichment: Path, output: Path) -> dict:
    articles = source / "articles.jsonl" if source.is_dir() else source
    links = set()
    images = set()
    with articles.open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            page = _url(row.get("url") or row.get("final_url"))
            if not page:
                continue
            for item in row.get("links") or []:
                target = _url(item.get("url")) if isinstance(item, dict) else ""
                if target:
                    links.add((page, target, "hyperlink", str(item.get("text") or ""), "", ""))
            for item in row.get("assets") or []:
                if not isinstance(item, dict):
                    continue
                target = _url(item.get("url"))
                if not target:
                    continue
                relation = "image" if item.get("role") == "inline_image" else "asset"
                links.add(
                    (
                        page,
                        target,
                        relation,
                        "",
                        str(item.get("alt") or ""),
                        str(item.get("caption") or ""),
                    )
                )
                if relation == "image":
                    images.add(target)
            for item in row.get("observed_refs") or []:
                if not isinstance(item, dict):
                    continue
                target = _url(item.get("url"))
                kind = str(item.get("kind") or "")
                if not target:
                    continue
                relation = (
                    "image"
                    if kind == "file"
                    and target.lower()
                    .split("?", 1)[0]
                    .endswith(
                        (
                            ".jpg",
                            ".jpeg",
                            ".png",
                            ".gif",
                            ".webp",
                            ".svg",
                            ".bmp",
                            ".tif",
                            ".tiff",
                            ".avif",
                        )
                    )
                    else "observed"
                )
                links.add(
                    (
                        page,
                        target,
                        relation,
                        str(item.get("text") or ""),
                        str(item.get("alt") or ""),
                        "",
                    )
                )
                if relation == "image":
                    images.add(target)
    output.mkdir(parents=True, exist_ok=True)
    with (output / "links.jsonl").open("w", encoding="utf-8") as out:
        for source_url, target_url, relation, anchor, alt, caption in sorted(links):
            out.write(
                json.dumps(
                    {
                        "source_url": source_url,
                        "target_url": target_url,
                        "relation": relation,
                        "anchor_text": anchor,
                        "alt": alt,
                        "caption": caption,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n"
            )
    with (output / "images.jsonl").open("w", encoding="utf-8") as out:
        for url in sorted(images):
            out.write(json.dumps({"url": url}, ensure_ascii=False) + "\n")
    return {"links": len(links), "images": len(images)}
