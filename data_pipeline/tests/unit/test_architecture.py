from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).parents[2]
SRC = ROOT / "src"
PACKAGE = SRC / "data_pipeline"
CONTEXTS = frozenset({"crawl", "enrich", "corpus", "domains"})
LAYERS = ("domain", "application", "infrastructure")
RANK = {layer: index for index, layer in enumerate(LAYERS)}
# Contexts whose per-layer rule is not enforced yet; emptied as each context is migrated.
MIGRATING: set[str] = set()
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


def placement(module: str) -> tuple[str, str | None]:
    """('crawl', 'domain'), ('shared', 'infrastructure'), ('cli', None) or ('crawl', None)."""
    parts = module.split(".")
    context = parts[1] if len(parts) > 1 else "data_pipeline"
    layer = parts[2] if len(parts) > 2 and parts[2] in LAYERS else None
    return context, layer


def allowed(importer: str, imported: str, migrating: set[str] | None = None) -> bool:
    migrating = MIGRATING if migrating is None else migrating
    if not imported.startswith("data_pipeline."):
        return True
    context, layer = placement(importer)
    target, target_layer = placement(imported)
    if context == "cli":
        return True
    if context == "shared":
        return target == "shared" and (layer == "infrastructure" or target_layer == "domain")
    if context in migrating:
        # Not split into layers yet: may use all of shared, and its old cross-context imports.
        return True
    if target == "shared":
        return target_layer == "domain" or layer == "infrastructure"
    if target != context:
        return False
    if layer is None:
        return True
    if target_layer is None:
        return False
    if layer == "infrastructure" and target_layer == "application":
        return imported.startswith(f"data_pipeline.{context}.application.ports")
    return RANK[target_layer] <= RANK[layer]


def all_modules() -> list[tuple[str, Path]]:
    return [(module_name(path), path) for path in sorted(PACKAGE.rglob("*.py"))]


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
        if placement(module)[1] == "domain" and placement(module)[0] not in MIGRATING
        for imported in sorted(imported_modules(path))
        if imported.split(".")[0] in IO_MODULES
    ]
    assert violations == []


def test_every_context_module_sits_in_a_layer() -> None:
    strays = [
        module
        for module, _ in all_modules()
        if placement(module)[0] in (CONTEXTS | {"shared"}) - MIGRATING
        and placement(module)[1] is None
        and not module.endswith(("__init__", ".domain", ".application", ".infrastructure"))
        and module.count(".") >= 2
    ]
    assert strays == []


def test_infrastructure_may_import_only_ports_from_application() -> None:
    infra = "data_pipeline.enrich.infrastructure.x"
    assert allowed(infra, "data_pipeline.enrich.application.ports", migrating=set())
    assert not allowed(infra, "data_pipeline.enrich.application.download", migrating=set())


def test_contexts_never_import_each_other() -> None:
    corpus, enrich = (
        "data_pipeline.corpus.application.package",
        "data_pipeline.enrich.domain.models",
    )
    assert not allowed(corpus, enrich, migrating=set())
    assert not allowed("data_pipeline.shared.domain.url", "data_pipeline.crawl.domain.access")
    assert allowed(
        "data_pipeline.cli.wiring", "data_pipeline.crawl.infrastructure.sqlite.state_store"
    )


def test_migrated_context_may_not_use_shared_infrastructure_outside_its_infrastructure() -> None:
    files = "data_pipeline.shared.infrastructure.files"
    assert not allowed("data_pipeline.enrich.domain.models", files, migrating=set())
    assert allowed("data_pipeline.enrich.infrastructure.files", files, migrating=set())
