from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer

from crawler import bootstrap
from crawler.cli import context

app = typer.Typer(
    help="Package, verify, query, publish and pull the corpus in MongoDB.", no_args_is_help=True
)


class Direction(StrEnum):
    incoming = "incoming"
    outgoing = "outgoing"


CorpusOption = Annotated[Path, typer.Option(help="Directory holding a packaged corpus.")]


@app.command()
def package(
    output: Annotated[Path, typer.Option(help="Directory to write the corpus package to.")],
) -> int:
    """Export the html, other and graph collections of MongoDB into a versioned corpus."""
    from crawler.application.corpus import package as corpus_package

    with bootstrap.mongo_database(context.build_settings()) as db:
        print(corpus_package.package_corpus(db, bootstrap.corpus_files(), output))
    return 0


@app.command()
def verify(corpus: CorpusOption) -> int:
    """Verify the files and checksums of a packaged corpus."""
    from crawler.application.corpus import package as corpus_package

    report = corpus_package.verify_corpus(corpus, bootstrap.corpus_files())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["valid"] else 3


@app.command()
def query(
    url: Annotated[str, typer.Option(help="Page URL whose links to list.")],
    direction: Annotated[
        Direction, typer.Option(help="incoming: links to the URL; outgoing: links from it.")
    ] = Direction.incoming,
    limit: Annotated[int, typer.Option(min=1, help="Most links to print.")] = 100,
) -> int:
    """Query the link graph in MongoDB."""
    from crawler.application.corpus import query as corpus_query

    with bootstrap.mongo_database(context.build_settings()) as db:
        result = corpus_query.query_links(
            db,
            url,
            direction="incoming" if direction is Direction.incoming else "outgoing",
            limit=limit,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


@app.command()
def publish(
    corpus: CorpusOption,
    config: Annotated[
        Path | None,
        typer.Option(help="Kaggle dataset metadata (default: config/kaggle-dataset.json)."),
    ] = None,
    update: Annotated[
        bool, typer.Option("--update", help="Publish a new version of the dataset.")
    ] = False,
    message: Annotated[str | None, typer.Option(help="Version notes.")] = None,
) -> int:
    """Publish the corpus to Kaggle."""
    from crawler.application.corpus import kaggle

    config_path = context.pick(config, context.current_paths().kaggle_config)
    result = kaggle.publish_corpus(
        corpus,
        config_path,
        bootstrap.dataset_publisher(),
        bootstrap.corpus_files(),
        update=update,
        message=message,
    )
    print(json.dumps(result, indent=2))
    return 0


@app.command()
def pull(
    lock: Annotated[
        Path | None, typer.Option(help="Corpus lock file (default: config/corpus.lock.json).")
    ] = None,
    destination: Annotated[
        Path | None, typer.Option(help="Where to download to (default: data/corpus/pulled).")
    ] = None,
    no_load: Annotated[
        bool, typer.Option("--no-load", help="Only download; do not load into MongoDB.")
    ] = False,
) -> int:
    """Download the locked corpus snapshot from Kaggle and load it into MongoDB."""
    from crawler.application.corpus import kaggle, load

    paths = context.current_paths()
    files = bootstrap.corpus_files()
    pulled = kaggle.pull_corpus(
        context.pick(lock, paths.corpus_lock),
        context.pick(destination, paths.corpus_pulled),
        bootstrap.dataset_publisher(),
        files,
    )
    print(pulled)
    if not no_load:
        with bootstrap.mongo_database(context.build_settings()) as db:
            print(json.dumps(load.load_corpus(pulled, db, files), indent=2))
    return 0
