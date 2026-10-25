from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import shutil
import tarfile
import tempfile
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from crawler.domain.corpus.manifest import Fingerprint
from crawler.infra.shared.files import swap_into_place


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


class LocalCorpusFiles:
    """The ``CorpusFiles`` port on the local disk."""

    def read_jsonl(self, path: Path) -> Iterator[dict[str, Any]]:
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                yield json.loads(line)

    def write_jsonl(self, path: Path, rows: Iterable[dict[str, Any]]) -> int:
        written = 0
        with path.open("w", encoding="utf-8") as stream:
            for row in rows:
                stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
                written += 1
        return written

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
        """A hard link when the disk allows: a package is tens of GB."""
        try:
            destination.hardlink_to(source)
        except OSError:
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
    def temporary(self, prefix: str, beside: Path) -> Iterator[Path]:
        beside.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=prefix, dir=beside.parent) as temp:
            yield Path(temp)

    def replace(self, staging: Path, output: Path) -> None:
        swap_into_place(staging, output)

    def install(self, folder: Path, destination: Path) -> None:
        """Move ``folder`` next to ``destination`` and swap it in."""
        destination.parent.mkdir(parents=True, exist_ok=True)
        staged = destination.with_name(f"{destination.name}.tmp-{os.getpid()}")
        shutil.rmtree(staged, ignore_errors=True)
        try:
            shutil.move(folder, staged)
            staged.chmod(0o755)
            swap_into_place(staged, destination)
        finally:
            shutil.rmtree(staged, ignore_errors=True)

    def pack(self, folder: Path, archive: Path, exclude: Iterable[str]) -> None:
        patterns = tuple(exclude)

        def keep(member: tarfile.TarInfo) -> tarfile.TarInfo | None:
            name = Path(member.name).name
            return None if any(fnmatch.fnmatch(name, p) for p in patterns) else member

        with tarfile.open(archive, "w:gz", compresslevel=6) as tar:
            for item in sorted(folder.iterdir()):
                tar.add(item, arcname=item.name, filter=keep)

    def unpack(self, archive: Path, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        staged = destination.with_name(f"{destination.name}.tmp-{os.getpid()}")
        shutil.rmtree(staged, ignore_errors=True)
        try:
            with tarfile.open(archive, "r:gz") as tar:
                tar.extractall(staged, filter="data")
            swap_into_place(staged, destination)
        finally:
            shutil.rmtree(staged, ignore_errors=True)
