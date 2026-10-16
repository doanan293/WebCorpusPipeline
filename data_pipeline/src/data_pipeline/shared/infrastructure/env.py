from __future__ import annotations

from dotenv import load_dotenv

from data_pipeline.shared.domain.config import ProjectPaths


def load_env(paths: ProjectPaths) -> None:
    """Load the project ``.env``; variables already exported by the shell win."""
    load_dotenv(dotenv_path=paths.env_file, override=False)
