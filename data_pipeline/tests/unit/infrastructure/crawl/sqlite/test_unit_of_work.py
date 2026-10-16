from __future__ import annotations

from pathlib import Path

import pytest

from data_pipeline.infrastructure.crawl.sqlite.unit_of_work import SqliteCrawlUnitOfWork


def open_uow(tmp_path: Path) -> SqliteCrawlUnitOfWork:
    seeds = tmp_path / "seeds.txt"
    seeds.write_text("a.test\n", encoding="utf-8")
    return SqliteCrawlUnitOfWork.open(
        tmp_path / "state", phase="crawl", input_path=seeds, semantic_config={}, runtime_config={}
    )


def test_repositories_share_one_connection(tmp_path: Path) -> None:
    uow = open_uow(tmp_path)
    try:
        uow.urls.complete_url(
            {"url": "https://a.test/", "status": "extracted"},
            article={"url": "https://a.test/", "text": "hello"},
        )
        assert uow.articles.article("https://a.test/") == {
            "url": "https://a.test/",
            "text": "hello",
        }
        assert uow.urls.is_finished("https://a.test/")
        assert uow.run.counts()["articles"] == 1
    finally:
        uow.close()


def test_transaction_rolls_back_every_repository(tmp_path: Path) -> None:
    uow = open_uow(tmp_path)
    try:
        with pytest.raises(RuntimeError, match="boom"), uow.transaction():
            uow.articles.set_article_payload("https://a.test/x", {"url": "https://a.test/x"})
            uow.owners.replace_content_owners([], {"c1": "https://a.test/x"})
            msg = "boom"
            raise RuntimeError(msg)
        assert uow.owners.content_owners() == {}
        assert uow.articles.article("https://a.test/x") is None
    finally:
        uow.close()


def test_close_releases_the_lock_after_an_error(tmp_path: Path) -> None:
    uow = open_uow(tmp_path)
    with pytest.raises(TypeError):
        # The article cannot be serialised: the write fails inside complete_url's transaction.
        uow.urls.complete_url(
            {"url": "https://a.test/", "status": "extracted"},
            article={"url": "https://a.test/", "payload": object()},
        )
    assert not uow.connection.in_transaction
    uow.close()

    assert not (tmp_path / "state" / "run.lock").exists()
    reopened = SqliteCrawlUnitOfWork.open_existing(tmp_path / "state")
    reopened.close()


def test_opening_a_state_without_refresh_adds_no_refresh_tables(tmp_path: Path) -> None:
    uow = open_uow(tmp_path)
    try:
        tables = {
            str(row[0])
            for row in uow.connection.execute("SELECT name FROM sqlite_master WHERE type='table';")
        }
    finally:
        uow.close()
    assert not {name for name in tables if name.startswith("refresh_")}


def test_writes_with_their_own_transaction_join_an_outer_one(tmp_path: Path) -> None:
    uow = open_uow(tmp_path)
    try:
        with pytest.raises(RuntimeError, match="boom"), uow.transaction():
            uow.urls.complete_url(
                {"url": "https://a.test/a", "status": "extracted"},
                article={"url": "https://a.test/a", "text": "a"},
            )
            uow.owners.release_content_owner("https://a.test/a", "c1")
            uow.urls.requeue([("https://a.test/a", {"url": "https://a.test/a"})])
            msg = "boom"
            raise RuntimeError(msg)
        assert uow.urls.url_record("https://a.test/a") is None
        assert uow.articles.article("https://a.test/a") is None
    finally:
        uow.close()
