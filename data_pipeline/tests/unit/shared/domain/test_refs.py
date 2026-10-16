import pytest

from data_pipeline.shared.domain.refs import classify_ref


@pytest.mark.parametrize(
    ("target", "role", "kind", "relation"),
    [
        ("https://a.test/page", None, None, "hyperlink"),
        ("https://a.test/a.png", "inline_image", None, "image"),
        ("https://a.test/a.pdf", "document_attachment", None, "asset"),
        ("https://a.test/a.png?x=1", None, "file", "image"),
        ("https://a.test/a.pdf", None, "file", "observed"),
        ("https://a.test/page", None, "html", "observed"),
    ],
)
def test_classify_ref_matches_graph_and_inventory(
    target: str, role: str | None, kind: str | None, relation: str
) -> None:
    assert classify_ref(target, role=role, kind=kind) == relation
