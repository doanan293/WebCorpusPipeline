from __future__ import annotations

from typing import Any

from crawler.application.storage.ports import CorpusStore
from crawler.domain.shared.change import UNCHANGED
from crawler.domain.storage.documents import to_write
from crawler.domain.storage.intro import build_intro_from_counts
from crawler.domain.storage.pictures import picture_format


def store_record(record: dict[str, Any], store: CorpusStore, now_iso: str) -> None:
    """The teacher's three storage tasks: the record, its edges, the intro of what it links to."""
    if record.get("change") in UNCHANGED:
        store.touch(record["url"], now_iso)
        return
    write = to_write(record, now_iso)
    if write.collection == "other":
        # The first page found linking to the document; kept once set.
        known = store.origin(record["url"])
        write.document["origin"] = (
            known if known is not None else store.first_linking_page(record["url"])
        )
    store.upsert(write.collection, write.document)
    store.replace_edges(record["url"], write.edges)
    targets = dict.fromkeys(edge["dest"] for edge in write.edges)
    if write.collection == "html":
        targets = {record["url"]: None, **targets}
    stored = store.existing_html(list(targets))
    counts = store.link_texts([url for url in targets if url in stored])
    store.set_intros(
        {url: build_intro_from_counts(counts.get(url, {})) for url in targets if url in stored}
    )
    if write.collection == "html":
        store.add_pictures(
            [_picture(image, record["url"], now_iso) for image in record.get("images") or []]
        )


def _picture(url: str, origin: str, now_iso: str) -> dict[str, Any]:
    """A ``picture`` record waiting for its bytes (``crawler pictures fetch`` fills ``b64``)."""
    return {
        "url": url,
        "origin": origin,
        "format": picture_format("", url),
        "b64": "",
        "size": None,
        "error": None,
        "fetched_at": None,
        "updated_at": now_iso,
    }
