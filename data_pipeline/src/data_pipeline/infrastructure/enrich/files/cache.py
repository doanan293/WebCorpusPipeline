from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

from data_pipeline.domain.enrich.models import ParseResult, jsonable
from data_pipeline.domain.enrich.serializers import parse_result_from_dict


def read_cached(root: Path, key: str) -> ParseResult | None:
    path = root / f"{key}.json"
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text())
        if raw.get("cache_key") != key:
            return None
        digest = raw.pop("checksum", None)
        payload = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if digest != hashlib.sha256(payload.encode()).hexdigest():
            return None
        return parse_result_from_dict(raw["result"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def write_cached(root: Path, key: str, result: ParseResult) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    raw = {"cache_key": key, "result": jsonable(result)}
    payload = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    raw["checksum"] = hashlib.sha256(payload.encode()).hexdigest()
    target = root / f"{key}.json"
    # Unique per writer: two workers may cache the same key at once.
    temp = target.with_name(f"{target.name}.{uuid.uuid4().hex}.tmp")
    temp.write_text(json.dumps(raw, ensure_ascii=False, sort_keys=True))
    temp.replace(target)
    return target


class FileParseCache:
    """Cloud parse results as checksummed ``<key>.json`` files under ``root``."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def read(self, key: str) -> ParseResult | None:
        return read_cached(self.root, key)

    def write(self, key: str, result: ParseResult) -> Path:
        return write_cached(self.root, key, result)

    def path_for(self, key: str) -> Path:
        return self.root / f"{key}.json"
