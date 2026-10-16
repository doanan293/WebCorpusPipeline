from __future__ import annotations

from llama_cloud.types.parsing_get_response import ParsingGetResponse

from data_pipeline.enrich.infrastructure.llamaparse import _page_texts


def _response(**fields: object) -> ParsingGetResponse:
    job = {"id": "job", "project_id": "project", "status": "COMPLETED"}
    return ParsingGetResponse.model_validate({"job": job, **fields})


def test_page_texts_reads_text_pages_by_page_number() -> None:
    response = _response(
        text={"pages": [{"page_number": 2, "text": "second"}, {"page_number": 1, "text": "first"}]}
    )

    assert _page_texts(response) == {1: "first", 2: "second"}


def test_page_texts_falls_back_to_markdown_pages() -> None:
    response = _response(
        markdown={
            "pages": [
                {"page_number": 1, "markdown": "# Title", "success": True},
                {"page_number": 2, "error": "boom", "success": False},
            ]
        }
    )

    assert _page_texts(response) == {1: "# Title"}


def test_page_texts_is_empty_without_page_results() -> None:
    assert _page_texts(_response(text_full="whole document")) == {}
