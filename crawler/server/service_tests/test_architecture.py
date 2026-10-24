"""services is the HTTP layer only: each service speaks for one block of the data_pipeline core."""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path(__file__).parents[1] / "src" / "services"
BLOCKS = frozenset({"mediator", "downloader", "reader", "merger", "storage"})
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
    """Whether ``importer`` (a services module) may import ``imported``."""
    if imported.startswith("data_pipeline.infrastructure"):
        return False  # adapters are built by data_pipeline.bootstrap only
    parts = importer.split(".")
    block = parts[2] if len(parts) > 2 and parts[1] == "api" else None
    if block not in BLOCKS:
        return True
    if imported.startswith("services."):
        return any(
            imported == name or imported.startswith(f"{name}.")
            for name in ("services.api.common", f"services.api.{block}")
        )
    if not imported.startswith("data_pipeline."):
        return True
    target = imported.split(".")
    if len(target) > 2 and target[1] in CORE_LAYERS:
        return target[2] in {block, "shared"}
    return imported == "data_pipeline.bootstrap" or imported.startswith("data_pipeline.bootstrap.")


def test_api_module_uses_only_its_block() -> None:
    assert allowed("services.api.downloader", "data_pipeline.application.downloader.fetch")
    assert allowed("services.api.downloader", "services.api.common")
    assert allowed("services.api.downloader", "data_pipeline.domain.shared.protocol")
    assert allowed("services.api.downloader", "data_pipeline.bootstrap")
    assert not allowed("services.api.downloader", "data_pipeline.application.reader.parse")
    assert not allowed("services.api.downloader", "services.api.reader")


def test_services_never_build_adapters() -> None:
    assert not allowed("services.cli", "data_pipeline.infrastructure.mediator.sqlite_queue")
    assert allowed("services.cli", "data_pipeline.bootstrap")


def test_every_import_follows_the_rules() -> None:
    broken = [
        f"{module_name(path)} -> {imported}"
        for path in sorted(PACKAGE.rglob("*.py"))
        for imported in sorted(imported_modules(path))
        if not allowed(module_name(path), imported)
    ]
    assert broken == []
