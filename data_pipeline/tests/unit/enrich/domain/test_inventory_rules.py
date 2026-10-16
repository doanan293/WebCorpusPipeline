from data_pipeline.enrich.domain.inventory_rules import clean_url, is_document_url, page_refs


def test_document_urls_and_cleaning() -> None:
    assert is_document_url("https://a.test/x.pdf?dl=1") and not is_document_url("https://a.test/x")
    assert clean_url(" https://a.test/a#frag ") == "https://a.test/a"


def test_page_refs_keeps_only_document_targets() -> None:
    row = {
        "links": [{"url": "https://a.test/f.pdf", "text": "Doc"}, {"url": "https://a.test/page"}],
        "assets": [{"url": "https://a.test/s.docx", "role": "document_attachment", "alt": "s"}],
    }
    refs = page_refs(row, "https://a.test/")
    assert [(r.target_url, r.relation) for r in refs] == [
        ("https://a.test/f.pdf", "hyperlink"),
        ("https://a.test/s.docx", "asset"),
    ]
