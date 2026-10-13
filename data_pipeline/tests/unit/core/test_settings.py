from data_pipeline.core.settings import load_api_keys, load_settings


def test_settings_resolve_config_and_data_from_pipeline(tmp_path, monkeypatch):
    config = tmp_path / "data_pipeline" / "config"
    config.mkdir(parents=True)
    (config / "data.yaml").write_text("download:\n  concurrency: 8\n  per_host: 2\n")
    monkeypatch.chdir(tmp_path.parent)

    settings = load_settings(tmp_path)

    assert settings.concurrency == 8
    assert settings.source_path == tmp_path / "data_pipeline/data/crawl/final"
    assert settings.work_path == tmp_path / "data_pipeline/data/enrichment"
    assert settings.repo_root == tmp_path


def test_pipeline_paths_keep_absolute_overrides(tmp_path):
    source = tmp_path / "external-source"
    work = tmp_path / "external-work"
    settings = load_settings(tmp_path, {"source": source, "work_dir": work})
    assert settings.source_path == source
    assert settings.work_path == work


def test_keys_stay_at_repository_root_when_running_in_pipeline(tmp_path, monkeypatch):
    import os

    for name in list(os.environ):
        if name.startswith("LLAMAPARSE_KEY"):
            monkeypatch.delenv(name)
    pipeline = tmp_path / "data_pipeline"
    pipeline.mkdir()
    (tmp_path / ".env").write_text("LLAMAPARSE_KEY1=root-test-key\n")
    (pipeline / ".env").write_text("LLAMAPARSE_KEY1=wrong-project-test-key\n")
    monkeypatch.chdir(pipeline)
    assert load_api_keys(tmp_path) == [("LLAMAPARSE_KEY1", "root-test-key")]
