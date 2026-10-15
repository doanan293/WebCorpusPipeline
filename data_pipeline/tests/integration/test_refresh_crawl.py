from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

URL = "https://news.test"


def run(
    root: Path, step: str, version: int, fail_paths: str = ""
) -> subprocess.CompletedProcess[str]:
    script = Path(__file__).parents[1] / "fixtures" / "run_refresh_fixture.py"
    return subprocess.run(  # noqa: S603 -- runs the repo's own fixture script
        [sys.executable, str(script), str(root), step, str(version), fail_paths],
        capture_output=True,
        text=True,
        check=False,
    )


def sessions(root: Path) -> list[list[str]]:
    result: list[list[str]] = []
    for line in (root / "requested_urls.txt").read_text(encoding="utf-8").splitlines():
        if line.startswith("# "):
            result.append([])
        elif line.strip():
            result[-1].append(line.strip())
    return result


def read_jsonl(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def run_dirs(root: Path) -> list[Path]:
    changes = root / "crawl" / "changes"
    if not changes.exists():
        return []
    return sorted(p for p in changes.iterdir() if p.is_dir() and not p.name.startswith("."))


def final_articles(root: Path) -> dict[str, dict[str, object]]:
    return {str(a["url"]): a for a in read_jsonl(root / "crawl" / "final" / "articles.jsonl")}


def test_refresh_fetches_hubs_and_new_pages_only(tmp_path: Path) -> None:
    initial = run(tmp_path, "initial", 1)
    assert initial.returncode == 0, initial.stderr
    assert f"{URL}/bai/a1" in final_articles(tmp_path)

    refresh = run(tmp_path, "refresh", 2)
    assert refresh.returncode == 0, refresh.stderr

    requested = set(sessions(tmp_path)[1])
    untouched = {f"{URL}/gioi-thieu", *(f"{URL}/bai/a{n}" for n in range(1, 5))}
    assert not requested & untouched
    [run_dir] = run_dirs(tmp_path)
    changes = {str(c["url"]): str(c["change"]) for c in read_jsonl(run_dir / "changes.jsonl")}
    assert {u for u, c in changes.items() if c == "new"} == {
        f"{URL}/bai/n1",
        f"{URL}/bai/n2",
        f"{URL}/bai/n3",
        f"{URL}/tin-tuc?page=3",
    }
    # Hubs are refetched and stored but never reported as modified.
    assert {u for u, c in changes.items() if c == "modified"} == {f"{URL}/bai/a5"}
    assert {str(a["url"]) for a in read_jsonl(run_dir / "articles.jsonl")} == set(changes)
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["outcomes"]["not_modified"] == 1
    final = final_articles(tmp_path)
    assert untouched | {f"{URL}/bai/n3"} <= set(final)
    assert "phiên bản 2" in str(final[f"{URL}/bai/a5"]["text"])
    readme = json.loads((tmp_path / "crawl" / "final" / "README.json").read_text(encoding="utf-8"))
    assert readme["last_refresh_run_id"] == run_dir.name == summary["run_id"]
    assert readme["crawl_id"] == summary["crawl_id"]


def test_second_refresh_without_site_changes_is_empty(tmp_path: Path) -> None:
    assert run(tmp_path, "initial", 1).returncode == 0
    assert run(tmp_path, "refresh", 2).returncode == 0
    second = run(tmp_path, "refresh", 2)
    assert second.returncode == 0, second.stderr

    latest = run_dirs(tmp_path)[-1]
    assert len(run_dirs(tmp_path)) == 2
    assert read_jsonl(latest / "changes.jsonl") == []
    summary = json.loads((latest / "summary.json").read_text(encoding="utf-8"))
    assert set(summary["outcomes"]) == {"not_modified"}


def test_failed_hub_refetch_keeps_previous_article(tmp_path: Path) -> None:
    assert run(tmp_path, "initial", 1).returncode == 0
    refresh = run(tmp_path, "refresh", 2, "/tin-tuc")

    assert refresh.returncode == 3, refresh.stderr
    text = str(final_articles(tmp_path)[f"{URL}/tin-tuc"]["text"])
    assert "a3" in text and "n1" not in text
    [run_dir] = run_dirs(tmp_path)
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert set(summary["outcomes"]) & {"http_500", "request_failed"}


def test_interrupted_refresh_continues_the_same_run(tmp_path: Path) -> None:
    assert run(tmp_path, "initial", 1).returncode == 0
    interrupted = run(tmp_path, "refresh_interrupt", 2)
    assert interrupted.returncode == 130, interrupted.stderr
    assert run_dirs(tmp_path) == []
    connection = sqlite3.connect(tmp_path / "crawl" / "state" / "index.sqlite3")
    run_id, status = connection.execute("SELECT run_id, status FROM refresh_runs").fetchone()
    done_before = {
        row[0] for row in connection.execute("SELECT url FROM refresh_targets WHERE done = 1")
    }
    connection.close()
    assert status == "interrupted"

    resumed = run(tmp_path, "refresh", 2)
    assert resumed.returncode == 0, resumed.stderr

    [run_dir] = run_dirs(tmp_path)
    assert run_dir.name == run_id
    assert not done_before & set(sessions(tmp_path)[2])
    changes = {str(c["url"]) for c in read_jsonl(run_dir / "changes.jsonl")}
    assert {f"{URL}/bai/n1", f"{URL}/bai/n2", f"{URL}/bai/n3"} <= changes
