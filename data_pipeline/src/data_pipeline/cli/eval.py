from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(help="Diagnostic evaluations.", no_args_is_help=True)


@app.command()
def extraction(
    fixtures: Annotated[Path, typer.Option(help="Directory of HTML fixtures with expectations.")],
    output: Annotated[Path, typer.Option(help="Where to write the evaluation report.")],
) -> int:
    """Score HTML content extraction against fixtures."""
    from data_pipeline.crawl.extract import evaluation

    print(json.dumps(evaluation.evaluate(fixtures, output), ensure_ascii=False, indent=2))
    return 0
