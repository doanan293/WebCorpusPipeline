from crawler.domain.reader.merge import merge_parts
from crawler.domain.reader.models import ExtractedPart, LocalResult, ParseResult


def test_cloud_pages_with_text_replace_local_pages() -> None:
    local = LocalResult(
        "sha",
        [
            ExtractedPart(1, "page", "", True, "pymupdf"),
            ExtractedPart(2, "page", "kept", False, "pymupdf"),
        ],
        ["w"],
        "pymupdf/1",
    )
    remote = ParseResult(
        "sha",
        [
            ExtractedPart(1, "page", "cloud", True, "llamaparse-fast"),
            ExtractedPart(2, "page", "", True, "llamaparse-fast"),
        ],
        "extracted",
        [],
        {"llamaparse": "fast/latest"},
    )

    merged = merge_parts(local, remote)

    assert [p.text for p in merged.parts] == ["cloud", "kept"]
    assert merged.reader_versions == {"local": "pymupdf/1", "llamaparse": "fast/latest"}
    assert merged.warnings == ["w"]
