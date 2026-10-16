from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from data_pipeline.domain.corpus.manifest import Fingerprint
from data_pipeline.infrastructure.shared.files import swap_into_place


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


class LocalCorpusFiles:
    """The ``CorpusFiles`` port on the local disk."""

    def articles_path(self, source: Path) -> Path:
        return source / "articles.jsonl" if source.is_dir() else source

    def read_jsonl(self, path: Path) -> Iterator[dict[str, Any]]:
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                yield json.loads(line)

    def read_json(self, path: Path) -> Any:
        return json.loads(path.read_text())

    def write_json(self, path: Path, value: Any) -> None:
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")

    def exists(self, path: Path) -> bool:
        return path.exists()

    def names(self, folder: Path) -> list[str]:
        return [item.name for item in sorted(folder.iterdir())]

    def fingerprint(self, path: Path) -> Fingerprint:
        return sha256_file(path), path.stat().st_size

    def line_count(self, path: Path) -> int:
        with path.open("rb") as stream:
            return sum(1 for _ in stream)

    def copy(self, source: Path, destination: Path) -> None:
        shutil.copy2(source, destination)

    def find(self, folder: Path, name: str) -> list[Path]:
        return list(folder.rglob(name))

    @contextmanager
    def staging(self, output: Path) -> Iterator[Path]:
        """A fresh, world-readable ``<output>.tmp-<pid>`` folder, removed afterwards."""
        output.parent.mkdir(parents=True, exist_ok=True)
        staging = output.with_name(f"{output.name}.tmp-{os.getpid()}")
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(mode=0o755)
        staging.chmod(0o755)
        try:
            yield staging
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    @contextmanager
    def temporary(self, prefix: str) -> Iterator[Path]:
        with tempfile.TemporaryDirectory(prefix=prefix) as temp:
            yield Path(temp)

    def replace(self, staging: Path, output: Path) -> None:
        swap_into_place(staging, output)

    def install_copy(self, folder: Path, destination: Path) -> None:
        """Copy ``folder`` next to ``destination`` and swap it in."""
        destination.parent.mkdir(parents=True, exist_ok=True)
        staged = destination.with_name(f"{destination.name}.tmp-{os.getpid()}")
        shutil.rmtree(staged, ignore_errors=True)
        try:
            shutil.copytree(folder, staged)
            staged.chmod(0o755)
            swap_into_place(staged, destination)
        finally:
            shutil.rmtree(staged, ignore_errors=True)
