from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from data_pipeline.enrich.parsing.pipeline import extract_documents_sync
from data_pipeline.enrich.export import export_enrichment
from data_pipeline.enrich.inventory import index_inventory, source_key
from data_pipeline.enrich.models import jsonable
from data_pipeline.core.settings import load_settings
from data_pipeline.enrich.state import WorkState, fingerprint_source, read_crawl_id


REBIND_HELP = (
    "accept a changed source for this work directory (needed once after upgrading, "
    "or when the source is not a refresh of the same crawl)"
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


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
        q.add_argument("--rebind-source", action="store_true", help=REBIND_HELP)
    q = sub.add_parser("export")
    q.add_argument("--source", default="data/crawl/final")
    q.add_argument("--work-dir", default="data/enrichment")
    q.add_argument("--output", required=True)
    q.add_argument("--rebind-source", action="store_true", help=REBIND_HELP)
    q = sub.add_parser("build-graph")
    q.add_argument("--source", default="data/crawl/final")
    q.add_argument("--work-dir", default="data/enrichment")
    q.add_argument("--output", required=True)
    q.add_argument("--rebind-source", action="store_true", help=REBIND_HELP)
    q = sub.add_parser("package")
    q.add_argument("--source", default="data/crawl/final")
    q.add_argument("--work-dir", default="data/enrichment")
    q.add_argument("--output", required=True)
    q.add_argument("--rebind-source", action="store_true", help=REBIND_HELP)
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
    q = sub.add_parser("evaluate-extraction")
    q.add_argument("--fixtures", required=True)
    q.add_argument("--output", required=True)
    q = sub.add_parser("reconcile-job")
    q.add_argument("--work-dir", default="data/enrichment")
    q.add_argument("--request-id", required=True)
    q.add_argument("--job-id", required=True)
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
            from data_pipeline.corpus.package import verify_corpus

            report = verify_corpus(Path(args.corpus))
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0 if report["valid"] else 3
        if args.command == "import-mongo":
            root = _repo_root()
            settings = load_settings(root)
            from data_pipeline.corpus.mongo import import_corpus

            print(json.dumps(import_corpus(Path(args.corpus), settings), indent=2))
            return 0
        if args.command == "query":
            root = _repo_root()
            settings = load_settings(root)
            from data_pipeline.corpus.query import query_links

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
            from data_pipeline.corpus.kaggle import publish_corpus
            from dotenv import load_dotenv

            root = _repo_root()
            load_dotenv(root / ".env", override=False)
            result = publish_corpus(
                Path(args.corpus), root / "data_pipeline" / args.config, args.update, args.message
            )
            print(json.dumps(result, indent=2))
            return 0
        if args.command == "pull":
            from data_pipeline.corpus.kaggle import pull_corpus
            from dotenv import load_dotenv

            root = _repo_root()
            load_dotenv(root / ".env", override=False)
            result = pull_corpus(
                root / "data_pipeline" / args.lock, root / "data_pipeline" / args.destination
            )
            print(result)
            return 0
        if args.command == "evaluate-extraction":
            from data_pipeline.crawl.extract.evaluation import evaluate

            print(
                json.dumps(
                    evaluate(Path(args.fixtures), Path(args.output)), ensure_ascii=False, indent=2
                )
            )
            return 0
        if args.command == "reconcile-job":
            root = _repo_root()
            settings = load_settings(root, {"work_dir": Path(args.work_dir)})
            import sqlite3

            conn = sqlite3.connect(settings.work_path / "state.sqlite3")
            row = conn.execute(
                "select * from cloud_jobs where request_id=?", (args.request_id,)
            ).fetchone()
            conn.close()
            if not row or row[3] != args.job_id:
                raise ValueError("request/job pair is not present in this work directory")
            print(
                json.dumps(
                    {"request_id": args.request_id, "job_id": args.job_id, "status": row[6]},
                    indent=2,
                )
            )
            return 0
        root, settings = _settings(args)
        source = settings.source_path
        work = settings.work_path
        fp = fingerprint_source(source)
        with WorkState.open(
            work, fp, crawl_id=read_crawl_id(source), rebind=args.rebind_source
        ) as state:
            state.recover()
            state.migrate_legacy_cursor(
                source_key(settings.project_root / "data" / "crawl" / "final")
            )
            if args.command == "inventory":
                summary = index_inventory(source, state)
                print(json.dumps(jsonable(summary), ensure_ascii=False, indent=2))
                return 0
            if args.command == "status":
                return _summary(state.summary(), args.json)
            if args.command == "download":
                from data_pipeline.enrich.download.downloader import run_downloads

                summary = asyncio.run(run_downloads(state, settings))
                return _summary(summary, args.json)
            if args.command == "extract":
                return _summary(
                    extract_documents_sync(state, settings, root), getattr(args, "json", False)
                )
            if args.command == "enrich-documents":
                if not state.inventory_done(source_key(source)):
                    index_inventory(source, state)
                from data_pipeline.enrich.download.downloader import run_downloads

                asyncio.run(run_downloads(state, settings))
                return _summary(
                    extract_documents_sync(state, settings, root), getattr(args, "json", False)
                )
            if args.command == "export":
                export_enrichment(state, Path(args.output))
                return 0
            if args.command == "build-graph":
                from data_pipeline.corpus.graph import build_graph

                build_graph(source, work, Path(args.output))
                return 0
            if args.command == "package":
                from data_pipeline.corpus.package import package_corpus

                package_corpus(source, work, Path(args.output))
                return 0
        return 0
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
