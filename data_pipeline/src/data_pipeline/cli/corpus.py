from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from data_pipeline.cli import context
from data_pipeline.cli.enrich import REBIND_HELP

app = typer.Typer(
    help="Package, verify, import and publish the processed corpus.", no_args_is_help=True
)

SourceOption = Annotated[
    Path | None, typer.Option(help="Crawl output to read (default: data/crawl/final).")
]
WorkDirOption = Annotated[
    Path | None, typer.Option(help="Enrichment work directory (default: data/enrichment).")
]
RebindOption = Annotated[bool, typer.Option("--rebind-source", help=REBIND_HELP)]
CorpusOption = Annotated[Path, typer.Option(help="Directory holding a packaged corpus.")]


@app.command("build-graph")
def build_graph(
    output: Annotated[Path, typer.Option(help="Directory to write the link graph to.")],
    source: SourceOption = None,
    work_dir: WorkDirOption = None,
    rebind_source: RebindOption = False,
) -> int:
    """Build the page/link graph from the crawl and enrichment output."""
    from data_pipeline.corpus import graph
    from data_pipeline.enrich import session

    settings = context.build_settings(source, work_dir)
    with session.open_work_state(settings, rebind=rebind_source):
        graph.build_graph(settings.source_path, output)
    return 0


@app.command()
def package(
    output: Annotated[Path, typer.Option(help="Directory to write the corpus package to.")],
    source: SourceOption = None,
    work_dir: WorkDirOption = None,
    rebind_source: RebindOption = False,
) -> int:
    """Package articles, documents, images and links into a versioned corpus."""
    from data_pipeline.corpus import package as corpus_package
    from data_pipeline.enrich import session

    settings = context.build_settings(source, work_dir)
    with session.open_work_state(settings, rebind=rebind_source):
        corpus_package.package_corpus(settings.source_path, settings.work_path, output)
    return 0


@app.command()
def verify(corpus: CorpusOption) -> int:
    """Verify the files and checksums of a packaged corpus."""
    from data_pipeline.corpus import package as corpus_package

    report = corpus_package.verify_corpus(corpus)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["valid"] else 3


@app.command("import-mongo")
def import_mongo(corpus: CorpusOption) -> int:
    """Import a packaged corpus into MongoDB."""
    from data_pipeline.corpus import mongo

    settings = context.build_settings(None, None)
    print(json.dumps(mongo.import_corpus(corpus, settings), indent=2))
    return 0


@app.command()
def query(
    snapshot: Annotated[str, typer.Option(help="Snapshot id to query.")],
    url: Annotated[str, typer.Option(help="Page URL whose links to list.")],
    incoming: Annotated[
        bool, typer.Option("--incoming", help="List links pointing to the URL.")
    ] = False,
    outgoing: Annotated[
        bool, typer.Option("--outgoing", help="List links leaving the URL.")
    ] = False,
) -> int:
    """Query the link graph stored in MongoDB (incoming links by default)."""
    from data_pipeline.corpus import query as corpus_query

    direction = "incoming" if incoming or not outgoing else "outgoing"
    settings = context.build_settings(None, None)
    result = corpus_query.query_links(settings, snapshot, url, direction)
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
    from data_pipeline.corpus import kaggle

    config_path = context.pick(config, context.current_paths().kaggle_config)
    print(
        json.dumps(
            kaggle.publish_corpus(corpus, config_path, update=update, message=message), indent=2
        )
    )
    return 0


@app.command()
def pull(
    lock: Annotated[
        Path | None, typer.Option(help="Corpus lock file (default: config/corpus.lock.json).")
    ] = None,
    destination: Annotated[
        Path | None, typer.Option(help="Where to download to (default: data/corpus/pulled).")
    ] = None,
) -> int:
    """Download the locked corpus snapshot from Kaggle."""
    from data_pipeline.corpus import kaggle

    paths = context.current_paths()
    print(
        kaggle.pull_corpus(
            context.pick(lock, paths.corpus_lock), context.pick(destination, paths.corpus_pulled)
        )
    )
    return 0
