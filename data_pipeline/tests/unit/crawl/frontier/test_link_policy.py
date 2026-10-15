import pytest

from data_pipeline.crawl.frontier.link_policy import (
    ClassifiedLink,
    classify_link,
    route_family_key,
    select_semantic_next,
)


@pytest.mark.parametrize(
    "url,text,kind",
    [
        ("https://a.test/article/123", "Chi tiết", "content"),
        ("https://a.test/view?id=123", "Xem", "content"),
        ("https://a.test/news?page=2", "2", "pagination"),
        ("https://a.test/search?q=x", "Tìm", "search_filter"),
        ("https://a.test/archive/2026/09", "Tháng 9", "calendar_archive"),
        ("https://a.test/logout", "Đăng xuất", "action_auth"),
        ("https://a.test/report.pdf", "PDF", "asset"),
    ],
)
def test_link_kinds(url: str, text: str, kind: str) -> None:
    assert classify_link(url, source_url="https://a.test/news", text=text).kind == kind


@pytest.mark.parametrize(
    ("url", "kind", "ordinal", "family_key"),
    [
        (
            "https://a.test/items?facet.topic=ai",
            "search_filter",
            None,
            "a.test/items",
        ),
        (
            "https://a.test/items?filters[topic]=ai",
            "search_filter",
            None,
            "a.test/items",
        ),
        (
            "https://a.test/items?filters[page]=whitepaper",
            "search_filter",
            None,
            "a.test/items",
        ),
        (
            "https://a.test/items?facet.offset=print",
            "search_filter",
            None,
            "a.test/items",
        ),
        (
            "https://a.test/items?f.start=early",
            "search_filter",
            None,
            "a.test/items",
        ),
        (
            "https://a.test/browse/author?value=Alice",
            "search_filter",
            None,
            "a.test/browse/author",
        ),
        (
            "https://a.test/items?spc.page=7",
            "search_filter",
            None,
            "a.test/items",
        ),
        (
            "https://a.test/items?f.topic=ai&spc.page=7",
            "search_filter",
            None,
            "a.test/items",
        ),
    ],
)
def test_generic_faceted_navigation_query_shapes_are_classified(
    url: str,
    kind: str,
    ordinal: int | None,
    family_key: str,
) -> None:
    link = classify_link(url, source_url="https://a.test/")

    assert (link.kind, link.ordinal, link.family_key) == (
        kind,
        ordinal,
        family_key,
    )


def test_namespaced_non_navigation_id_remains_content() -> None:
    link = classify_link(
        "https://a.test/profile?profile.id=42",
        source_url="https://a.test/",
    )

    assert (link.kind, link.ordinal, link.family_key) == ("content", None, None)


@pytest.mark.parametrize("query_key", ["dir", "orderby", "sortby"])
def test_generic_sort_query_keys_are_navigation(query_key: str) -> None:
    link = classify_link(
        f"https://a.test/products?{query_key}=price",
        source_url="https://a.test/",
    )

    assert (link.kind, link.family_key) == ("search_filter", "a.test/products")


def test_route_family_preserves_content_ids() -> None:
    assert route_family_key("https://a.test/article/123", "content") is None
    assert route_family_key("https://a.test/view?id=123", "content") is None
    assert (
        route_family_key("https://a.test/news?page=123", "pagination") == "a.test/news?page={page}"
    )
    assert (
        route_family_key("https://a.test/news/page/123", "pagination") == "a.test/news/page/{page}"
    )
    assert (
        route_family_key("https://a.test/archive/2026/09", "calendar_archive")
        == "a.test/archive/{year}/{month}"
    )


@pytest.mark.parametrize(("text", "rel"), [("", ("shortlink",)), ("FSS tuyển thực tập sinh", ())])
def test_wordpress_shortlink_post_id_is_search_filter(text: str, rel: tuple[str, ...]) -> None:
    link = classify_link(
        "https://a.test/?p=33214", source_url="https://a.test/news/", text=text, rel=rel
    )

    assert link.kind == "search_filter"


def test_embedded_resource_hint_overrides_extensionless_page_shape() -> None:
    link = classify_link(
        "https://a.test/index.php?second=cronjobs&p=random",
        source_url="https://a.test/news/",
        text="cron",
        resource_kind_hint="file",
    )

    assert link.kind == "asset"


def test_numeric_p_anchor_remains_pagination() -> None:
    link = classify_link(
        "https://a.test/?p=2",
        source_url="https://a.test/",
        text="2",
    )

    assert (link.kind, link.ordinal) == ("pagination", 2)


def test_p_anchor_with_next_label_is_pagination() -> None:
    link = classify_link(
        "https://a.test/?p=2",
        source_url="https://a.test/",
        text="Older Posts",
    )

    assert (link.kind, link.ordinal) == ("pagination", 2)


def test_shortlink_relation_overrides_other_pagination_signals() -> None:
    link = classify_link(
        "https://a.test/?p=33214",
        source_url="https://a.test/news/an-article/",
        text="33214",
        rel=("next", "shortlink"),
    )

    assert link.kind == "search_filter"


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("Hướng dẫn đăng ký thi VSTEP online", "content"),
        ("Đăng nhập", "action_auth"),
        ("Level Design Intern", "content"),
    ],
)
def test_action_text_matches_whole_words_in_short_labels(text: str, kind: str) -> None:
    link = classify_link("https://a.test/news/item-1", source_url="https://a.test/", text=text)

    assert link.kind == kind


def test_self_link_is_not_classified_as_action() -> None:
    link = classify_link(
        "https://a.test/news/item-1#top",
        source_url="https://a.test/news/item-1",
        text="Đăng nhập",
    )

    assert link.kind == "content"


@pytest.mark.parametrize("query", ["f.author=x&spc.page=1", "spc.sf=dc.title"])
def test_dspace_facets_are_search_filters(query: str) -> None:
    link = classify_link(f"https://a.test/browse-items?{query}", source_url="https://a.test/")

    assert link.kind == "search_filter"


@pytest.mark.parametrize(
    "query",
    [
        "p_p_id=82&p_p_lifecycle=1&_82_struts_action=x",
        "redirect=/x",
        "inheritRedirect=true",
        "_101_INSTANCE_abc_page=2",
    ],
)
def test_liferay_portlet_actions_are_search_filters(query: str) -> None:
    link = classify_link(f"https://a.test/web/guest/news?{query}", source_url="https://a.test/")

    assert link.kind == "search_filter"


@pytest.mark.parametrize(
    ("url", "kind"),
    [
        ("https://clc.test/?p=836", "search_filter"),
        ("https://clc.test/?p=abc", "content"),
        ("https://clc.test/page/?p=2&cat=1", "content"),
    ],
)
def test_wordpress_numeric_shortlink_is_search_filter(url: str, kind: str) -> None:
    assert classify_link(url, source_url="https://clc.test/").kind == kind


def test_selects_only_one_semantic_next() -> None:
    links = (
        ClassifiedLink(
            "https://a.test/news?page=2", "pagination", "a.test/news?page={page}", 2, False
        ),
        ClassifiedLink(
            "https://a.test/news?page=3", "pagination", "a.test/news?page={page}", 3, True
        ),
        ClassifiedLink(
            "https://a.test/news?page=99", "pagination", "a.test/news?page={page}", 99, False
        ),
    )
    selected = select_semantic_next(links, current_ordinal=2)
    assert selected is not None
    assert selected.url.endswith("page=3")
