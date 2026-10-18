from lxml import html as lxml_html

from data_pipeline.domain.parser.extract.blocks import blocks_from_article


def paragraphs(markup: str) -> list[str]:
    blocks = blocks_from_article(lxml_html.fragment_fromstring(markup))
    return [block["text"] for block in blocks if block["type"] == "paragraph"]


def test_inline_children_stay_in_one_paragraph() -> None:
    markup = '<div>Sinh viên <b>cần</b> nộp <a href="/x">hồ sơ</a>.<p>Đoạn 2</p></div>'

    assert paragraphs(markup) == ["Sinh viên cần nộp hồ sơ.", "Đoạn 2"]


def test_br_splits_lines_inside_paragraph() -> None:
    assert paragraphs("<div>Dòng 1<br>Dòng <i>2</i></div>") == ["Dòng 1\nDòng 2"]
    assert paragraphs("<div><p>Địa chỉ:<br>  Số 1 Đại Cồ Việt</p></div>") == [
        "Địa chỉ:\nSố 1 Đại Cồ Việt"
    ]
