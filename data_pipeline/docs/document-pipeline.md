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
