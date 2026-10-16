from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer

from data_pipeline.presentation.cli import context, wiring
from data_pipeline.presentation.cli.context import RebindOption, SourceOption, WorkDirOption

app = typer.Typer(
    help="Package, verify, import and publish the processed corpus.", no_args_is_help=True
)


class Direction(StrEnum):
    incoming = "incoming"
    outgoing = "outgoing"


CorpusOption = Annotated[Path, typer.Option(help="Directory holding a packaged corpus.")]


@app.command("build-graph")
def build_graph(
    output: Annotated[Path, typer.Option(help="Directory to write the link graph to.")],
    source: SourceOption = None,
) -> int:
    """Build the page/link graph from the crawl output alone."""
    from data_pipeline.application.corpus import graph

    files = wiring.corpus_files()
    articles = files.articles_path(context.build_settings(source, None).source_path)
    graph.build_graph(files.read_jsonl(articles), output, wiring.edge_index())
    return 0


@app.command()
def package(
    output: Annotated[Path, typer.Option(help="Directory to write the corpus package to.")],
    source: SourceOption = None,
    work_dir: WorkDirOption = None,
    rebind_source: RebindOption = False,
) -> int:
    """Package articles, documents, images and links into a versioned corpus."""
    from data_pipeline.application.corpus import package as corpus_package

    settings = context.build_settings(source, work_dir)
    with wiring.work_state(settings, rebind=rebind_source) as state:
        corpus_package.package_corpus(
            settings.source_path,
            state,
            output,
            export_documents=wiring.export_documents(state),
            files=wiring.corpus_files(),
            open_index=wiring.edge_index(),
        )
    return 0


@app.command()
def verify(corpus: CorpusOption) -> int:
    """Verify the files and checksums of a packaged corpus."""
    from data_pipeline.application.corpus import package as corpus_package

    report = corpus_package.verify_corpus(corpus, wiring.corpus_files())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["valid"] else 3


@app.command("import-mongo")
def import_mongo(
    corpus: CorpusOption,
    force: Annotated[
        bool, typer.Option("--force", help="Import again even if the snapshot is ready.")
    ] = False,
) -> int:
    """Import a packaged corpus into MongoDB."""
    from data_pipeline.application.corpus import mongo

    with wiring.mongo_database(context.build_settings(None, None)) as db:
        result = mongo.import_corpus(corpus, db, wiring.corpus_files(), force=force)
        print(json.dumps(result, indent=2))
    return 0


@app.command()
def query(
    snapshot: Annotated[str, typer.Option(help="Snapshot id to query.")],
    url: Annotated[str, typer.Option(help="Page URL whose links to list.")],
    direction: Annotated[
        Direction, typer.Option(help="incoming: links to the URL; outgoing: links from it.")
    ] = Direction.incoming,
    limit: Annotated[int, typer.Option(min=1, help="Most links to print.")] = 100,
) -> int:
    """Query the link graph of a ready snapshot in MongoDB."""
    from data_pipeline.application.corpus import query as corpus_query

    with wiring.mongo_database(context.build_settings(None, None)) as db:
        result = corpus_query.query_links(
            db,
            snapshot,
            url,
            direction="incoming" if direction is Direction.incoming else "outgoing",
            limit=limit,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


@app.command("prune-mongo")
def prune_mongo(
    keep: Annotated[str, typer.Option(help="Snapshot id to keep; every other one is deleted.")],
) -> int:
    """Delete every MongoDB snapshot except the one to keep."""
    from data_pipeline.application.corpus import mongo

    with wiring.mongo_database(context.build_settings(None, None)) as db:
        print(json.dumps(mongo.prune_snapshots(db, keep), indent=2))
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
    from data_pipeline.application.corpus import kaggle

    config_path = context.pick(config, context.current_paths().kaggle_config)
    result = kaggle.publish_corpus(
        corpus,
        config_path,
        wiring.dataset_publisher(),
        wiring.corpus_files(),
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
) -> int:
    """Download the locked corpus snapshot from Kaggle."""
    from data_pipeline.application.corpus import kaggle

    paths = context.current_paths()
    print(
        kaggle.pull_corpus(
            context.pick(lock, paths.corpus_lock),
            context.pick(destination, paths.corpus_pulled),
            wiring.dataset_publisher(),
            wiring.corpus_files(),
        )
    )
    return 0
