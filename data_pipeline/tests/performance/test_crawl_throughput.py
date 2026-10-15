from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.fixtures.latency_download_handler import PAGES_PER_HOST
from tests.fixtures.run_throughput_fixture import HOSTS


@pytest.mark.performance
def test_crawl_throughput(tmp_path: Path) -> None:
    """Pages per minute of a full crawl of two generated hosts with 20 ms response latency."""
    script = Path(__file__).parents[1] / "fixtures" / "run_throughput_fixture.py"
    result = subprocess.run(  # noqa: S603 -- runs the repo's own fixture script
        [sys.executable, str(script), str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-2000:]

    manifest = json.loads((tmp_path / "crawl" / "manifest.json").read_text(encoding="utf-8"))
    pages = manifest["counts"]["url_status_extracted"]
    elapsed = manifest["metrics"]["elapsed_seconds"]
    rate = pages / elapsed * 60
    print(f"\ncrawl throughput: {pages} pages in {elapsed:.1f}s = {rate:.0f} pages/min")  # noqa: T201
    assert pages == len(HOSTS) * (PAGES_PER_HOST + 1)  # pages 1..N plus each homepage
