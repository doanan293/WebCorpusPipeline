from __future__ import annotations

from urllib.parse import urldefrag

# The slide's Mongo collections, which are also the files of a corpus package.
CORPUS_COLLECTIONS = ("html", "picture", "other", "graph")


# The folders of data/ kept beside Mongo (crawl state, raw HTML, document files, the check
# script), so a fresh clone needs no recrawl. Each is packed as one archive; the Mongo dumps in
# submission/ are left out, being the collections again.
DATA_FOLDERS = ("state", "crawl", "enrichment", "submission")
DATA_EXCLUDE = ("*.archive.gz", "*.archive.gz.sha256")


def data_archive(folder: str) -> str:
    """The archive of a data folder. Not ``.tar.gz``: Kaggle unpacks those into folders."""
    return f"data-{folder}.tar.gz.bin"


def collection_file(name: str) -> str:
    return f"{name}.jsonl"


def normalize_query_url(url: str) -> str:
    """The graph stores URLs without fragments; normalise a query the same way."""
    return urldefrag(url.strip())[0]
