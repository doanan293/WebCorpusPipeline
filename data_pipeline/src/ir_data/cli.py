from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .enrichment import extract_documents_sync
from .enrichment_export import export_enrichment
from .inventory import index_inventory
from .models import jsonable
from .settings import load_settings
from .state import WorkState, fingerprint_source


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ir-data", description="Build and publish the data_pipeline corpus"
    )
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("inventory", "download", "extract", "enrich-documents", "status"):
        q = sub.add_parser(name)
        q.add_argument("--source", default="data/crawl/final")
        q.add_argument("--work-dir", default="data/enrichment")
        q.add_argument("--json", action="store_true")
    q = sub.add_parser("export")
    q.add_argument("--source", default="data/crawl/final")
    q.add_argument("--work-dir", default="data/enrichment")
    q.add_argument("--output", required=True)
    q = sub.add_parser("build-graph")
    q.add_argument("--source", default="data/crawl/final")
    q.add_argument("--work-dir", default="data/enrichment")
    q.add_argument("--output", required=True)
    q = sub.add_parser("package")
    q.add_argument("--source", default="data/crawl/final")
    q.add_argument("--work-dir", default="data/enrichment")
    q.add_argument("--output", required=True)
    q = sub.add_parser("verify")
    q.add_argument("--corpus", required=True)
    q = sub.add_parser("import-mongo")
    q.add_argument("--corpus", required=True)
    q = sub.add_parser("query")
    q.add_argument("--snapshot", required=True)
    q.add_argument("--url", required=True)
    q.add_argument("--incoming", action="store_true")
    q.add_argument("--outgoing", action="store_true")
    q = sub.add_parser("publish")
    q.add_argument("--corpus", required=True)
    q.add_argument("--config", default="config/kaggle-dataset.json")
    q.add_argument("--update", action="store_true")
    q.add_argument("--message")
    q = sub.add_parser("pull")
    q.add_argument("--lock", default="config/corpus.lock.json")
    q.add_argument("--destination", default="data/corpus/pulled")
    return p


def _settings(args):
    root = _repo_root()
    return root, load_settings(root, {"source": Path(args.source), "work_dir": Path(args.work_dir)})


def _summary(summary, as_json=False):
    value = jsonable(summary)
    print(
        json.dumps(value, ensure_ascii=False, indent=2)
        if as_json
        else "downloaded={downloaded} parsed={parsed} failed={failed} pending={pending} quota_blocked={quota_blocked}".format(
            **value
        )
    )
    return summary.exit_code or (4 if summary.pending or summary.quota_blocked else 0)


def main(argv: list[str] | None = None) -> int:
    try:
        args = parser().parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2
    try:
        if args.command == "verify":
            from .package import verify_corpus

            report = verify_corpus(Path(args.corpus))
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0 if report["valid"] else 3
        if args.command == "import-mongo":
            root = _repo_root()
            settings = load_settings(root)
            from .mongo_store import import_corpus

            print(json.dumps(import_corpus(Path(args.corpus), settings), indent=2))
            return 0
        if args.command == "query":
            root = _repo_root()
            settings = load_settings(root)
            from .query import query_links

            direction = "incoming" if args.incoming or not args.outgoing else "outgoing"
            print(
                json.dumps(
                    query_links(settings, args.snapshot, args.url, direction),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        if args.command == "publish":
            from .kaggle_io import publish_corpus

            root = _repo_root()
            result = publish_corpus(
                Path(args.corpus), root / "data_pipeline" / args.config, args.update, args.message
            )
            print(json.dumps(result, indent=2))
            return 0
        if args.command == "pull":
            from .kaggle_io import pull_corpus

            root = _repo_root()
            result = pull_corpus(
                root / "data_pipeline" / args.lock, root / "data_pipeline" / args.destination
            )
            print(result)
            return 0
        root, settings = _settings(args)
        source = settings.source_path
        work = settings.work_path
        fp = fingerprint_source(source)
        with WorkState.open(work, fp) as state:
            state.recover()
            if args.command == "inventory":
                summary = index_inventory(source, state)
                print(json.dumps(jsonable(summary), ensure_ascii=False, indent=2))
                return 0
            if args.command == "status":
                return _summary(state.summary(), args.json)
            if args.command == "download":
                from .download import run_downloads

                summary = asyncio.run(run_downloads(state, settings))
                return _summary(summary, args.json)
            if args.command == "extract":
                return _summary(
                    extract_documents_sync(state, settings, root), getattr(args, "json", False)
                )
            if args.command == "enrich-documents":
                if not state.inventory_done():
                    index_inventory(source, state)
                from .download import run_downloads

                asyncio.run(run_downloads(state, settings))
                return _summary(
                    extract_documents_sync(state, settings, root), getattr(args, "json", False)
                )
            if args.command == "export":
                export_enrichment(state, Path(args.output))
                return 0
            if args.command == "build-graph":
                from .graph import build_graph

                build_graph(source, work, Path(args.output))
                return 0
            if args.command == "package":
                from .package import package_corpus

                package_corpus(source, work, Path(args.output))
                return 0
        return 0
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
