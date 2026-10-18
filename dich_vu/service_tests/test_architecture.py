"""dich_vu is the HTTP layer only: each service speaks for one block of the data_pipeline core."""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path(__file__).parents[1] / "src" / "dich_vu"
BLOCKS = frozenset({"controller", "fetcher", "parser", "aggregator", "storage"})
CORE_LAYERS = frozenset({"domain", "application"})


def module_name(path: Path) -> str:
    parts = path.relative_to(PACKAGE.parent).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def imported_modules(path: Path) -> set[str]:
    imported: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module)
            imported.update(f"{node.module}.{alias.name}" for alias in node.names)
    return imported


def allowed(importer: str, imported: str) -> bool:
    """Whether ``importer`` (a dich_vu module) may import ``imported``."""
    if imported.startswith("data_pipeline.infrastructure"):
        return False  # adapters are built by data_pipeline.bootstrap only
    parts = importer.split(".")
    block = parts[2] if len(parts) > 2 and parts[1] == "api" else None
    if block not in BLOCKS:
        return True
    if imported.startswith("dich_vu."):
        return any(
            imported == name or imported.startswith(f"{name}.")
            for name in ("dich_vu.api.common", f"dich_vu.api.{block}")
        )
    if not imported.startswith("data_pipeline."):
        return True
    target = imported.split(".")
    if len(target) > 2 and target[1] in CORE_LAYERS:
        return target[2] in {block, "shared"}
    return imported == "data_pipeline.bootstrap" or imported.startswith("data_pipeline.bootstrap.")


def test_api_module_uses_only_its_block() -> None:
    assert allowed("dich_vu.api.fetcher", "data_pipeline.application.fetcher.fetch")
    assert allowed("dich_vu.api.fetcher", "dich_vu.api.common")
    assert allowed("dich_vu.api.fetcher", "data_pipeline.domain.shared.protocol")
    assert allowed("dich_vu.api.fetcher", "data_pipeline.bootstrap")
    assert not allowed("dich_vu.api.fetcher", "data_pipeline.application.parser.parse")
    assert not allowed("dich_vu.api.fetcher", "dich_vu.api.parser")


def test_services_never_build_adapters() -> None:
    assert not allowed("dich_vu.cli", "data_pipeline.infrastructure.controller.sqlite_queue")
    assert allowed("dich_vu.cli", "data_pipeline.bootstrap")


def test_every_import_follows_the_rules() -> None:
    broken = [
        f"{module_name(path)} -> {imported}"
        for path in sorted(PACKAGE.rglob("*.py"))
        for imported in sorted(imported_modules(path))
        if not allowed(module_name(path), imported)
    ]
    assert broken == []
