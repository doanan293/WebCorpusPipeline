from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).parents[2]
SRC = ROOT / "src"
PACKAGE = SRC / "data_pipeline"

# Importing module prefix -> prefixes it must not import.
FORBIDDEN: dict[str, tuple[str, ...]] = {
    "data_pipeline.core": (
        "data_pipeline.crawl",
        "data_pipeline.enrich",
        "data_pipeline.corpus",
        "data_pipeline.domains",
        "data_pipeline.cli",
    ),
    "data_pipeline.crawl": (
        "data_pipeline.enrich",
        "data_pipeline.corpus",
        "data_pipeline.domains",
        "data_pipeline.cli",
    ),
    "data_pipeline.crawl.spider": ("data_pipeline.crawl.run",),
    "data_pipeline.crawl.state": ("data_pipeline.crawl.run", "data_pipeline.crawl.spider"),
    "data_pipeline.crawl.extract": (
        "data_pipeline.crawl.run",
        "data_pipeline.crawl.spider",
        "data_pipeline.crawl.state",
    ),
    "data_pipeline.crawl.frontier": ("data_pipeline.crawl.run", "data_pipeline.crawl.spider"),
    "data_pipeline.enrich": (
        "data_pipeline.crawl",
        "data_pipeline.corpus",
        "data_pipeline.domains",
        "data_pipeline.cli",
    ),
    "data_pipeline.corpus": ("data_pipeline.crawl", "data_pipeline.domains", "data_pipeline.cli"),
    "data_pipeline.domains": (
        "data_pipeline.crawl",
        "data_pipeline.enrich",
        "data_pipeline.corpus",
        "data_pipeline.cli",
    ),
}


def module_name(path: Path) -> str:
    parts = path.relative_to(SRC).with_suffix("").parts
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


def under(name: str, prefix: str) -> bool:
    return name == prefix or name.startswith(f"{prefix}.")


def test_layer_dependencies() -> None:
    violations: list[str] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        module = module_name(path)
        for importer, banned in FORBIDDEN.items():
            if not under(module, importer):
                continue
            violations.extend(
                f"{module} -> {imported}"
                for imported in sorted(imported_modules(path))
                if any(under(imported, prefix) for prefix in banned)
            )
    assert violations == []
