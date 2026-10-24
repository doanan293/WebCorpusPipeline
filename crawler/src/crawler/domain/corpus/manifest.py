from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

# Files a corpus package holds besides manifest.json: one per Mongo collection of the slide.
PAYLOAD = ("html.jsonl", "picture.jsonl", "other.jsonl", "graph.jsonl")
# 2.0: the teacher's format (picture collection, graph and html intro, other origin).
SCHEMA_VERSION = "2.0"
MANIFEST = "manifest.json"

type Fingerprint = tuple[str, int]
"""(sha256, size) of one file."""


def snapshot_id(files: Mapping[str, Any]) -> str:
    """The snapshot id: a digest of the manifest's file list."""
    return hashlib.sha256(
        json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def build_manifest(files: dict[str, Any], counts: dict[str, int]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "snapshot_id": snapshot_id(files),
        "files": files,
        "counts": counts,
    }


def verify_manifest(
    manifest: Mapping[str, Any], present: Mapping[str, Fingerprint | None]
) -> list[str]:
    """Compare a manifest with the files present (name -> fingerprint, None if not hashed)."""
    version = manifest.get("schema_version")
    if version != SCHEMA_VERSION:
        return [f"schema_version {version} is not {SCHEMA_VERSION}"]
    files: Mapping[str, Any] = manifest.get("files", {})
    listed = set(files) | {MANIFEST}
    errors = [f"unlisted file {name}" for name in sorted(present) if name not in listed]
    for name, meta in files.items():
        fingerprint = present.get(name)
        if fingerprint is None:
            errors.append(f"missing {name}")
            continue
        sha256, size = fingerprint
        if size != meta.get("size"):
            errors.append(f"size mismatch {name}")
        if sha256 != meta.get("sha256"):
            errors.append(f"checksum mismatch {name}")
    if snapshot_id(files) != manifest.get("snapshot_id"):
        errors.append("snapshot_id mismatch")
    return errors
