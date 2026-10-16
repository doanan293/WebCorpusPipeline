from __future__ import annotations

import json
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

STATE_VERSION = 4


def _resumed_manifest(
    manifest: dict[str, Any],
    *,
    phase: str,
    input_sha256: str,
    semantic_config: Mapping[str, object],
    runtime_config: Mapping[str, object],
) -> dict[str, Any]:
    if manifest.get("state_version") != STATE_VERSION:
        msg = "resume state version does not match saved crawl"
        raise ValueError(msg)
    if manifest.get("phase") != phase:
        msg = "resume phase does not match saved run"
        raise ValueError(msg)
    if manifest.get("input_sha256") != input_sha256:
        msg = "resume input does not match saved crawl"
        raise ValueError(msg)
    if manifest.get("semantic_config") != dict(semantic_config):
        msg = "resume semantic configuration does not match saved crawl"
        raise ValueError(msg)
    history = manifest.get("runtime_history", [])
    if not history or history[-1] != runtime_config:
        history.append(dict(runtime_config))
        manifest["runtime_history"] = history
    manifest["resumed_from_status"] = manifest.get("status")
    manifest["status"] = "running"
    return manifest


def manifest_for_open(
    root: Path,
    *,
    resume: bool,
    phase: str,
    input_sha256: str,
    semantic_config: Mapping[str, object],
    runtime_config: Mapping[str, object],
) -> dict[str, Any]:
    """Return the manifest for a new crawl state or the validated one being resumed."""
    manifest_path = root / "manifest.json"
    if manifest_path.exists():
        if not resume:
            msg = f"crawl state already exists: {root}"
            raise FileExistsError(msg)
        return _resumed_manifest(
            json.loads(manifest_path.read_text(encoding="utf-8")),
            phase=phase,
            input_sha256=input_sha256,
            semantic_config=semantic_config,
            runtime_config=runtime_config,
        )
    if resume:
        msg = f"cannot resume non-existent crawl state: {root}"
        raise ValueError(msg)
    return {
        "state_version": STATE_VERSION,
        "phase": phase,
        "input_sha256": input_sha256,
        "semantic_config": dict(semantic_config),
        "runtime_history": [dict(runtime_config)],
        "status": "running",
        "started_at": time.time(),
    }
