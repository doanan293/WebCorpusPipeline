# Corpus schema

The package produced by `ir-data package` contains the original `articles.jsonl`,
`urls.txt`, and `README.json`, plus `documents.jsonl`, `images.jsonl`,
`links.jsonl`, `extraction-report.json`, and `manifest.json`.

`documents.jsonl` has one record per downloaded document URL. It contains the
original and final URL, SHA-256, MIME, size, `extraction_status`, plain-text
parts (`page`, `sheet`, `slide`, or `document`), parser versions, warnings, and
referrers. The output intentionally keeps reading order and provenance without
trying to reproduce document layout.

`images.jsonl` is an URL catalogue. Image bytes are not included. Each link
record stores `source_url`, `target_url`, `relation`, `anchor_text`, `alt`, and
`caption`; descriptions with different values remain separate edges. Targets
outside the crawled pages are valid graph vertices.

MongoDB adds `snapshot_id` to every collection and uses a deterministic `_id`.
Readers only use datasets with status `ready`; repeated imports of one snapshot
are idempotent.
