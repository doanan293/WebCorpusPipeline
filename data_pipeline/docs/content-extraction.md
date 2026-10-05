# HTML content extraction

The crawler parses each HTML response once. It first prefers semantic
`article`/`main` blocks, then elements whose id/class hints at content, and
finally the body. The score rewards useful text and paragraphs and penalizes
link-heavy blocks and navigation hints. The selected block is cleaned of
navigation, forms, scripts, headers, footers, and hidden elements.

The record keeps title, published date, author, summary, readable text, basic
content blocks, and source metadata. Metadata is not invented when a page does
not provide it. Dates retain their original precision and timezone text.

Links and asset references are collected before cleaning. `observed_refs`
preserves page-to-target provenance, while `assets` identifies images and
document attachments. Image bytes are not downloaded by this enrichment step;
the graph retains their referring pages, alt text, and captions.

The fixture evaluator in `src/hust_crawler/crawl/evaluation.py` reports token
precision/recall/F1 and title accuracy. It is a diagnostic report, not a claim
that the existing historical crawl has HTML bytes available for reprocessing.
