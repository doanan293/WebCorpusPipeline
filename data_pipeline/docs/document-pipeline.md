# Document pipeline

Run commands from `data_pipeline/`:

```bash
uv sync --extra producer
uv run ir-data inventory --source data/crawl/final --work-dir data/enrichment
uv run ir-data download --work-dir data/enrichment
uv run ir-data extract --work-dir data/enrichment
uv run ir-data export --work-dir data/enrichment --output data/corpus/enrichment
```

Inventory reads the existing JSONL and keeps a separate SQLite checkpoint. The
downloader streams at most 64 files globally and two per host. A completed file
is verified locally and skipped on later runs. An interrupted `.part` file is
continued only when a strong ETag and a valid HTTP Range response agree;
otherwise only that file is restarted.

Local parsers are used first: PyMuPDF for PDF, `python-docx` for DOCX, local
LibreOffice conversion for legacy DOC, `openpyxl`/`xlrd` for spreadsheets, and
`python-pptx` for presentations. The output is plain text with basic page,
sheet, slide, or document boundaries. LlamaParse uses `fast/latest` only for a
visual PDF page that has no useful local text. It never runs for a blank page or
for text that is merely short or poorly formatted.

If a run stops, repeat the same command. `ir-data status --json` reports
downloaded, parsed, failed, pending, and quota-blocked work.

Inventory keeps one cursor per source file, so a refreshed crawl can be added
with `ir-data inventory --source data/crawl/changes/<run_id>`. For pages marked
`modified` in that folder's `changes.jsonl`, the previous outgoing references
are replaced by the new ones. A work directory is bound to one crawl through
the `crawl_id` in the source's `README.json` or `summary.json`; refreshed
sources of the same crawl are accepted, and `--rebind-source` adopts a changed
source explicitly.
