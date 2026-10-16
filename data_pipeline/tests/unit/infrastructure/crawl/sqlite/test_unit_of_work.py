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
        uow.urls.merge_url({"url": "https://a.test/", "payload": object()})
    uow.close()

    assert not (tmp_path / "state" / "run.lock").exists()
    reopened = SqliteCrawlUnitOfWork.open_existing(tmp_path / "state")
    reopened.close()
