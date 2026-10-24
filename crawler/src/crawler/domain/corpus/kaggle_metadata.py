from __future__ import annotations

from typing import Any

REQUIRED = ("owner", "slug", "title", "subtitle", "description", "license_name")
KEYWORDS = ["information retrieval", "HUST", "Vietnamese", "documents"]


def dataset_metadata(config: dict[str, Any]) -> dict[str, Any]:
    """The ``dataset-metadata.json`` Kaggle expects, from the project's dataset config."""
    missing = [key for key in REQUIRED if not str(config.get(key, "")).strip()]
    if missing:
        raise ValueError("Kaggle metadata missing: " + ", ".join(missing))
    return {
        "title": config["title"],
        "id": dataset_ref(config),
        "subtitle": config["subtitle"],
        "description": config["description"],
        "licenses": [{"name": config["license_name"]}],
        "keywords": list(KEYWORDS),
    }


def dataset_ref(config: dict[str, Any]) -> str:
    return f"{config['owner']}/{config['slug']}"
