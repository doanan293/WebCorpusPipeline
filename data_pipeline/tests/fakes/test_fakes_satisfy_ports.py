from __future__ import annotations

from typing import Any

from data_pipeline.corpus.application import ports as corpus_ports
from data_pipeline.enrich.application import ports
from tests.fakes.corpus import FakeCorpusDatabase, FakePublisher
from tests.fakes.enrich import (
    FakeArticleSource,
    FakeCloudParser,
    FakeLocalParser,
    FakeTransport,
    FakeWorkState,
)


def _never(*_: object) -> Any:
    raise AssertionError


def _check(instance: object, port: type) -> None:
    attrs = port.__protocol_attrs__  # type: ignore[attr-defined]
    missing = [name for name in attrs if not hasattr(instance, name)]
    assert missing == [], f"{type(instance).__name__} lacks {missing}"


def test_enrich_fakes_satisfy_their_ports() -> None:
    # The annotated assignments are checked by pyrefly; _check catches a missing member at runtime.
    state: ports.WorkState = FakeWorkState()
    source: ports.ArticleSource = FakeArticleSource([], "c1")
    local: ports.LocalParser = FakeLocalParser(_never)
    cloud: ports.CloudParser = FakeCloudParser(_never)
    transport: ports.HttpTransport = FakeTransport()
    _check(state, ports.WorkState)
    _check(source, ports.ArticleSource)
    _check(local, ports.LocalParser)
    _check(cloud, ports.CloudParser)
    _check(transport, ports.HttpTransport)


def test_corpus_fakes_satisfy_their_ports() -> None:
    db: corpus_ports.CorpusDatabase = FakeCorpusDatabase()
    publisher: corpus_ports.DatasetPublisher = FakePublisher()
    _check(db, corpus_ports.CorpusDatabase)
    _check(publisher, corpus_ports.DatasetPublisher)
