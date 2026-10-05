from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from .models import ParseResult, jsonable
from .serializers import parse_result_from_dict


def cache_key(sha256: str, indices: list[int], parser: str, settings_digest: str = "") -> str:
    payload = json.dumps([sha256, sorted(indices), parser, settings_digest], separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


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
    temp = target.with_suffix(".tmp")
    temp.write_text(json.dumps(raw, ensure_ascii=False, sort_keys=True))
    os.replace(temp, target)
    return target
