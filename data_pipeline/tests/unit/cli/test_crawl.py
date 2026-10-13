from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from data_pipeline.cli.main import main
from data_pipeline.core.paths import ProjectPaths
from data_pipeline.crawl.run import discovery as run_discovery
from data_pipeline.crawl.run import full as run_full


def write_complete_config(
    directory: Path, contact: str = "ops@example.org", name: str = "crawler.yaml"
) -> Path:
    values = yaml.safe_load(Path("config/crawler.yaml").read_text(encoding="utf-8"))
    values["contact"] = contact
    path = directory / name
    path.write_text(yaml.safe_dump(values), encoding="utf-8")
    return path


@pytest.fixture
def seeds(tmp_path: Path) -> Path:
    path = tmp_path / "seeds.txt"
    path.write_text("a.test\n", encoding="utf-8")
    return path


@pytest.fixture
def config(tmp_path: Path) -> Path:
    return write_complete_config(tmp_path)


@pytest.fixture
def observed(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    captured: dict[str, object] = {}

    def capture_run(**kwargs: object) -> int:
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(run_full, "run_crawl", capture_run)
    return captured


def crawl(seeds: Path, tmp_path: Path, config: Path, *extra: str) -> int:
    return main(
        [
            "crawl",
            "run",
            "--input",
            str(seeds),
            "--output",
            str(tmp_path / "crawl"),
            "--config",
            str(config),
            *extra,
        ]
    )


def test_accepts_mixed_seeds_and_frontier_limits(
    tmp_path: Path, seeds: Path, config: Path, observed: dict[str, object]
) -> None:
    code = crawl(
        seeds,
        tmp_path,
        config,
        "--assets",
        "all",
        "--max-query-variants-per-path",
        "12",
        "--pagination-empty-pages",
        "4",
        "--max-urls-per-host",
        "50000",
        "--max-total-urls",
        "300000",
    )

    assert code == 0
    options = observed["options"]
    assert options.assets == "all"
    assert options.max_query_variants_per_path == 12
    assert options.pagination_empty_pages == 4
    assert options.max_urls_per_host == 50_000
    assert options.max_total_urls == 300_000


def test_defaults_come_from_the_config_file(
    tmp_path: Path, seeds: Path, config: Path, observed: dict[str, object]
) -> None:
    assert crawl(seeds, tmp_path, config) == 0

    assert observed["options"].assets == "content-only"
    assert observed["reuse_content_from"] is None
    for name in (
        "resume",
        "retry_failed",
        "retry_truncated",
        "retry_access_gates",
        "retry_policy_skips",
        "refresh",
    ):
        assert observed[name] is False


def test_asset_choices_are_validated(tmp_path: Path, seeds: Path, config: Path) -> None:
    assert crawl(seeds, tmp_path, config, "--assets", "documents") == 2


@pytest.mark.parametrize(
    "flag",
    ["--retry-failed", "--retry-truncated", "--retry-access-gates", "--retry-policy-skips"],
)
def test_retry_flag_requires_resume(
    flag: str, tmp_path: Path, seeds: Path, config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert crawl(seeds, tmp_path, config, flag) == 2
    assert f"{flag} requires --resume" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("flag", "name"),
    [
        ("--retry-failed", "retry_failed"),
        ("--retry-truncated", "retry_truncated"),
        ("--retry-access-gates", "retry_access_gates"),
        ("--retry-policy-skips", "retry_policy_skips"),
    ],
)
def test_retry_flag_is_passed_to_the_runner_with_resume(
    flag: str,
    name: str,
    tmp_path: Path,
    seeds: Path,
    config: Path,
    observed: dict[str, object],
) -> None:
    assert crawl(seeds, tmp_path, config, "--resume", flag) == 0

    assert observed["resume"] is True
    assert observed[name] is True


def test_passes_seed_set_to_the_unified_runner(
    tmp_path: Path, seeds: Path, config: Path, observed: dict[str, object]
) -> None:
    seeds.write_text("a.test\nhttps://b.test/page\n", encoding="utf-8")

    assert crawl(seeds, tmp_path, config) == 0

    assert observed["input_path"] == seeds
    assert observed["output"] == tmp_path / "crawl"
    assert observed["seeds"].recursive_hostnames == frozenset({"a.test"})
    assert observed["seeds"].exact_urls == ("https://b.test/page",)


def test_passes_lightweight_content_source_to_the_runner(
    tmp_path: Path, seeds: Path, config: Path, observed: dict[str, object]
) -> None:
    code = crawl(
        seeds, tmp_path, config, "--assets", "all", "--reuse-content-from", str(tmp_path / "src")
    )

    assert code == 0
    assert observed["reuse_content_from"] == tmp_path / "src"


def test_rejects_reuse_source_without_all_assets(
    tmp_path: Path, seeds: Path, config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = crawl(seeds, tmp_path, config, "--reuse-content-from", str(tmp_path / "src"))

    assert code == 2
    assert "requires --assets all" in capsys.readouterr().err


def test_reports_runner_validation_errors(
    tmp_path: Path,
    seeds: Path,
    config: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def reject_run(**kwargs: object) -> int:
        msg = "source crawl is still running"
        raise ValueError(msg)

    monkeypatch.setattr(run_full, "run_crawl", reject_run)

    code = crawl(seeds, tmp_path, config)

    assert code == 2
    assert "source crawl is still running" in capsys.readouterr().err


def test_missing_seed_file_is_an_error(
    tmp_path: Path, config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert crawl(tmp_path / "missing.txt", tmp_path, config) == 2
    assert "input file not found" in capsys.readouterr().err


def test_loads_contact_from_the_project_env_file(
    project: ProjectPaths,
    tmp_path: Path,
    seeds: Path,
    observed: dict[str, object],
) -> None:
    project.env_file.write_text("CRAWLER_CONTACT=ops@example.org\n", encoding="utf-8")
    config = write_complete_config(tmp_path, "replace-with-operator-contact@example.invalid")

    assert crawl(seeds, tmp_path, config) == 0

    assert observed["config"].contact == "ops@example.org"


def test_config_defaults_to_the_project_crawler_yaml(
    project: ProjectPaths, tmp_path: Path, seeds: Path, observed: dict[str, object]
) -> None:
    (project.root / "config").mkdir()
    write_complete_config(project.root / "config", "ops@example.org")
    code = main(["crawl", "run", "-i", str(seeds), "-o", str(tmp_path / "crawl")])

    assert code == 0
    assert observed["config"].contact == "ops@example.org"


@pytest.mark.parametrize(
    "extra",
    [
        ["--resume"],
        ["--reuse-content-from", "other"],
        ["--retry-failed"],
        ["--retry-truncated"],
        ["--retry-access-gates"],
        ["--retry-policy-skips"],
    ],
)
def test_refresh_rejects_incompatible_flags(
    tmp_path: Path,
    seeds: Path,
    config: Path,
    extra: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert crawl(seeds, tmp_path, config, "--refresh", *extra) == 2
    assert "--refresh cannot be combined" in capsys.readouterr().err


def test_discover_loads_contact_and_runs_discovery(
    project: ProjectPaths,
    tmp_path: Path,
    seeds: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project.env_file.write_text("CRAWLER_CONTACT=ops@example.org\n", encoding="utf-8")
    config = write_complete_config(tmp_path, "replace-with-operator-contact@example.invalid")
    captured: dict[str, object] = {}

    def capture_run(**kwargs: object) -> int:
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(run_discovery, "run_discovery", capture_run)

    code = main(
        [
            "crawl",
            "discover",
            "--input",
            str(seeds),
            "--output",
            str(tmp_path / "discovery"),
            "--config",
            str(config),
        ]
    )

    assert code == 0
    assert captured["config"].contact == "ops@example.org"
    assert captured["resume"] is False


def test_crawl_help_mentions_resume_and_refresh(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["crawl", "run", "--help"]) == 0
    text = capsys.readouterr().out.lower()
    assert "--resume" in text
    assert "--refresh" in text
