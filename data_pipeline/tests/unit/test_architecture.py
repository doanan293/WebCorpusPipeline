from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).parents[2]
SRC = ROOT / "src"
PACKAGE = SRC / "data_pipeline"
# The package is split by layer first, then by context:
#   data_pipeline.<layer>.<context>...   and   data_pipeline.presentation.cli...
LAYERS = ("domain", "application", "infrastructure")
RANK = {layer: index for index, layer in enumerate(LAYERS)}
CONTEXTS = frozenset({"crawl", "enrich", "corpus", "hosts"})
SHARED = "shared"
PRESENTATION = "presentation"
IO_MODULES = frozenset(
    {
        "sqlite3",
        "scrapy",
        "scrapy_playwright",
        "twisted",
        "aiohttp",
        "pymongo",
        "playwright",
        "llama_cloud",
        "subprocess",
        "shutil",
        "tempfile",
        "os",
        "yaml",
        "dotenv",
        "socket",
    }
)


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


def placement(module: str) -> tuple[str | None, str | None]:
    """(layer, context): ('domain', 'crawl'), ('infrastructure', 'shared'), ('presentation', 'cli')."""
    parts = module.split(".")
    layer = parts[1] if len(parts) > 1 else None
    context = parts[2] if len(parts) > 2 else None
    return layer, context


def allowed(importer: str, imported: str) -> bool:
    """Whether ``importer`` may import ``imported`` (both dotted module names)."""
    if not imported.startswith("data_pipeline."):
        return True
    layer, context = placement(importer)
    target_layer, target_context = placement(imported)
    if layer == PRESENTATION:
        return True
    if target_layer == PRESENTATION or layer not in RANK or target_layer not in RANK:
        return False
    # Contexts never import each other; every context may use shared, shared uses only itself.
    if target_context is not None and target_context not in {context, SHARED}:
        return False
    if context == SHARED and target_context not in {SHARED, None}:
        return False
    # Infrastructure reaches the application layer only through its context's ports.
    if layer == "infrastructure" and target_layer == "application":
        return imported.startswith(f"data_pipeline.application.{context}.ports")
    return RANK[target_layer] <= RANK[layer]


def all_modules() -> list[tuple[str, Path]]:
    return [(module_name(path), path) for path in sorted(PACKAGE.rglob("*.py"))]


def is_stray(module: str) -> bool:
    """A module that is not inside ``<layer>.<context>`` (or ``presentation.cli``)."""
    if module.count(".") < 2:
        return module.count(".") == 1 and placement(module)[0] not in {*LAYERS, PRESENTATION}
    layer, context = placement(module)
    if layer == PRESENTATION:
        return context != "cli"
    return layer not in RANK or context not in CONTEXTS | {SHARED}


def test_dependency_rule() -> None:
    violations = [
        f"{module} -> {imported}"
        for module, path in all_modules()
        for imported in sorted(imported_modules(path))
        if not allowed(module, imported)
    ]
    assert violations == []


def test_domain_modules_do_no_io() -> None:
    violations = [
        f"{module} -> {imported}"
        for module, path in all_modules()
        if placement(module)[0] == "domain"
        for imported in sorted(imported_modules(path))
        if imported.split(".")[0] in IO_MODULES
    ]
    assert violations == []


def test_every_module_sits_in_a_layer_and_a_context() -> None:
    assert [module for module, _ in all_modules() if is_stray(module)] == []


def test_a_module_outside_the_layers_is_a_stray() -> None:
    for module in (
        "data_pipeline.helpers",
        "data_pipeline.domain.helpers",
        "data_pipeline.domain.legacy.models",
        "data_pipeline.presentation.web",
    ):
        assert is_stray(module), module
    for module in (
        "data_pipeline",
        "data_pipeline.domain",
        "data_pipeline.domain.crawl",
        "data_pipeline.infrastructure.shared.files",
        "data_pipeline.presentation.cli.main",
    ):
        assert not is_stray(module), module


def test_layers_point_inwards() -> None:
    assert allowed("data_pipeline.application.crawl.run_crawl", "data_pipeline.domain.crawl.seeds")
    assert not allowed(
        "data_pipeline.domain.crawl.seeds", "data_pipeline.application.crawl.run_crawl"
    )
    assert not allowed(
        "data_pipeline.application.enrich.download",
        "data_pipeline.infrastructure.enrich.files.download_files",
    )


def test_infrastructure_may_import_only_ports_from_application() -> None:
    infra = "data_pipeline.infrastructure.enrich.files.cache"
    assert allowed(infra, "data_pipeline.application.enrich.ports")
    assert not allowed(infra, "data_pipeline.application.enrich.download")
    assert not allowed(infra, "data_pipeline.application.crawl.ports")


def test_contexts_never_import_each_other() -> None:
    assert not allowed(
        "data_pipeline.application.corpus.package", "data_pipeline.domain.enrich.models"
    )
    assert allowed("data_pipeline.application.corpus.package", "data_pipeline.domain.shared.url")
    assert not allowed("data_pipeline.domain.shared.url", "data_pipeline.domain.crawl.access")
    assert allowed(
        "data_pipeline.presentation.cli.wiring",
        "data_pipeline.infrastructure.crawl.sqlite.state_store",
    )


def test_only_infrastructure_uses_shared_infrastructure() -> None:
    files = "data_pipeline.infrastructure.shared.files"
    assert not allowed("data_pipeline.domain.enrich.models", files)
    assert not allowed("data_pipeline.application.enrich.download", files)
    assert allowed("data_pipeline.infrastructure.enrich.files.cache", files)
