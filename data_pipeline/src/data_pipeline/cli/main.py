from __future__ import annotations

import sys
from collections.abc import Sequence
from typing import NoReturn

import typer

from data_pipeline import bootstrap
from data_pipeline.cli import context, corpus, crawl, domains, enrich, errors, eval

app = typer.Typer(
    name="data-pipeline",
    help="Crawl, enrich, package and publish the HUST document corpus.",
    no_args_is_help=True,
    add_completion=False,
)
app.add_typer(crawl.app, name="crawl")
app.add_typer(enrich.app, name="enrich")
app.add_typer(corpus.app, name="corpus")
app.add_typer(domains.app, name="domains")
app.add_typer(eval.app, name="eval")


@app.callback()
def _load_project_env() -> None:
    bootstrap.load_env(context.current_paths())


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and translate failures into exit codes (2 usage/error, 130 interrupt)."""
    try:
        result = app(
            args=list(argv) if argv is not None else None,
            prog_name="data-pipeline",
            standalone_mode=False,
        )
    except typer.TyperException as exc:
        show = getattr(exc, "show", None)
        if callable(show):
            show()
        else:
            errors.fail(str(exc))
        return exc.exit_code
    except (typer.Abort, KeyboardInterrupt):
        return 130
    except ModuleNotFoundError as exc:
        message = errors.missing_extra_message(exc)
        if message is None:
            raise
        return errors.fail(message)
    except (FileNotFoundError, FileExistsError, ValueError, RuntimeError) as exc:
        return errors.fail(str(exc))
    except Exception as exc:
        message = errors.external_failure_message(exc)
        if message is None:
            raise
        return errors.fail(message, exit_code=1)
    return result if isinstance(result, int) else 0


def run() -> NoReturn:
    sys.exit(main())
