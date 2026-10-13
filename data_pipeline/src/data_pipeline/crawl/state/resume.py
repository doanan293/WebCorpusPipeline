from __future__ import annotations

import json
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

_LEGACY_ACCESS_POLICY_REVISION = 1
_CURRENT_ACCESS_POLICY_REVISION = 2

POLICY_RETRY_REASONS = frozenset(
    {"robots_disallowed", "login_required", "captcha_blocked", "access_denied"}
)


ACCESS_POLICY_SEMANTIC_KEYS = frozenset(
    {
        "access_policy_revision",
        "browser_user_agent",
        "robots_txt_obey",
        "user_agent_name",
        "user_agent_version",
    }
)


def validate_policy_migration(saved: Mapping[str, object], effective: Mapping[str, object]) -> None:
    saved_revision = int(saved.get("access_policy_revision", 1))
    effective_revision = int(effective.get("access_policy_revision", 1))
    if (
        saved_revision != _LEGACY_ACCESS_POLICY_REVISION
        or effective_revision != _CURRENT_ACCESS_POLICY_REVISION
    ):
        msg = (
            "unsupported access policy revision migration: "
            f"{saved_revision} -> {effective_revision}"
        )
        raise ValueError(msg)
    if saved.get("robots_txt_obey", True) is not True:
        msg = "policy migration requires saved robots_txt_obey=true"
        raise ValueError(msg)
    if effective.get("robots_txt_obey") is not False:
        msg = "policy migration requires effective robots_txt_obey=false"
        raise ValueError(msg)

    saved_other = {
        key: value for key, value in saved.items() if key not in ACCESS_POLICY_SEMANTIC_KEYS
    }
    effective_other = {
        key: value for key, value in effective.items() if key not in ACCESS_POLICY_SEMANTIC_KEYS
    }
    if saved_other != effective_other:
        changed = sorted(
            key
            for key in set(saved_other) | set(effective_other)
            if saved_other.get(key) != effective_other.get(key)
        )
        raise ValueError(
            "resume semantic configuration differs outside access policy: " + ", ".join(changed)
        )


def scheduled_retry(record: Mapping[str, object]) -> dict[str, object]:
    retry = dict(record)
    retry.update({"status": "scheduled", "frontier_action": "scheduled"})
    for key in ("completed", "final_url", "http_status", "reason"):
        retry.pop(key, None)
    return retry


LEGACY_V4_SEMANTIC_DEFAULTS = {
    "robots_txt_obey": True,
    "cookies_enabled": False,
    "redirect_max_times": 20,
    "user_agent_name": "HUSTPublicCrawler",
    "user_agent_version": "1.0",
}

LEGACY_V4_RECLASSIFIED_RUNTIME_KEYS = frozenset(
    {
        "download_timeout_seconds",
        "retry_times",
        "retry_backoff_base_seconds",
        "retry_max_delay_seconds",
    }
)


def normalize_legacy_semantic_config(snapshot: Mapping[str, object]) -> dict[str, object]:
    normalized = {
        name: value
        for name, value in snapshot.items()
        if name not in LEGACY_V4_RECLASSIFIED_RUNTIME_KEYS
    }
    for name, value in LEGACY_V4_SEMANTIC_DEFAULTS.items():
        normalized.setdefault(name, value)
    return normalized


def validate_resume_workflow(state_root: Path, expected: str) -> None:
    manifest_path = state_root / "manifest.json"
    if not manifest_path.exists():
        return
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    actual = manifest.get("semantic_config", {}).get("crawl_strategy")
    if actual != expected:
        msg = f"cannot resume {actual or 'legacy'} state as {expected}; use a new output directory"
        raise ValueError(msg)


STATE_VERSION = 4


def _resumed_manifest(
    manifest: dict[str, Any],
    *,
    phase: str,
    input_sha256: str,
    semantic_config: dict[str, object],
    runtime_config: dict[str, object],
    allow_policy_migration: bool,
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
    saved_semantic = normalize_legacy_semantic_config(manifest.get("semantic_config", {}))
    effective_semantic = normalize_legacy_semantic_config(semantic_config)
    if saved_semantic != effective_semantic:
        if not allow_policy_migration:
            msg = "resume semantic configuration does not match saved crawl"
            raise ValueError(msg)
        validate_policy_migration(saved_semantic, effective_semantic)
        migrations = list(manifest.get("policy_migrations", []))
        if not any(
            entry.get("to_revision") == effective_semantic.get("access_policy_revision")
            for entry in migrations
            if isinstance(entry, dict)
        ):
            migrations.append(
                {
                    "from_revision": saved_semantic.get("access_policy_revision", 1),
                    "to_revision": effective_semantic["access_policy_revision"],
                    "timestamp": time.time(),
                }
            )
        manifest["policy_migrations"] = migrations
        manifest["semantic_config"] = semantic_config
    history = manifest.get("runtime_history", [])
    if not history or history[-1] != runtime_config:
        history.append(runtime_config)
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
    semantic_config: dict[str, object],
    runtime_config: dict[str, object],
    allow_policy_migration: bool,
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
            allow_policy_migration=allow_policy_migration,
        )
    if resume:
        msg = f"cannot resume non-existent crawl state: {root}"
        raise ValueError(msg)
    return {
        "state_version": STATE_VERSION,
        "phase": phase,
        "input_sha256": input_sha256,
        "semantic_config": semantic_config,
        "runtime_history": [runtime_config],
        "status": "running",
        "started_at": time.time(),
    }
