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
    from data_pipeline.domain.parser.extract.evaluation import Fixture, evaluate

    report = evaluate(
        Fixture(
            name=path.name,
            html=path.with_suffix(".html").read_bytes(),
            expected=json.loads(path.read_text(encoding="utf-8")),
        )
        for path in sorted(fixtures.glob("*.json"))
    )
    text = json.dumps(report, ensure_ascii=False, indent=2)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0
