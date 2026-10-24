from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).parents[2]
SRC = ROOT / "src"
PACKAGE = SRC / "data_pipeline"
# The package is split by layer first, then by context:
#   data_pipeline.<layer>.<context>...   and   data_pipeline.cli...
LAYERS = ("domain", "application", "infrastructure")
RANK = {layer: index for index, layer in enumerate(LAYERS)}
BLOCKS = frozenset({"mediator", "downloader", "reader", "merger", "storage"})
CONTEXTS = frozenset({"corpus", "hosts", *BLOCKS})
SHARED = "shared"
# Entry points; bootstrap.py is the composition root. runner wires the blocks into one process
# (for the cli), the one place allowed to use several blocks at once.
OUTER = frozenset({"cli", "runner"})
BOOTSTRAP = "bootstrap"
IO_MODULES = frozenset(
    {
        "sqlite3",
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

# pathlib methods and builtins that touch the file system; domain code takes the bytes instead.
FILE_CALLS = frozenset(
    {"open", "read_text", "read_bytes", "write_text", "write_bytes", "mkdir", "unlink", "glob"}
)


def module_name(path: Path) -> str:
    parts = path.relative_to(SRC).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def imported_modules(path: Path) -> set[str]:
    imported: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        # Relative imports are skipped here; ruff's ban-relative-imports = "all" forbids them.
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module)
            imported.update(f"{node.module}.{alias.name}" for alias in node.names)
    return imported


def placement(module: str) -> tuple[str | None, str | None]:
    """(layer, context): ('domain', 'reader'), ('cli', None), ('bootstrap', None)."""
    parts = module.split(".")
    layer = parts[1] if len(parts) > 1 else None
    context = parts[2] if len(parts) > 2 and layer in RANK else None
    return layer, context


def allowed(importer: str, imported: str) -> bool:
    """Whether ``importer`` may import ``imported`` (both dotted module names)."""
    if not imported.startswith("data_pipeline."):
        return True
    layer, context = placement(importer)
    target_layer, target_context = placement(imported)
    if layer == BOOTSTRAP:
        return target_layer not in OUTER
    # Entry points call use cases and bootstrap; they never build adapters themselves.
    if layer in OUTER:
        runs = layer == "cli" and target_layer == "runner"
        return target_layer != "infrastructure" and (runs or target_layer not in OUTER - {layer})
    if target_layer in OUTER or target_layer == BOOTSTRAP:
        return False
    if layer not in RANK or target_layer not in RANK:
        return False
    # Contexts never import each other; every context may use shared, shared uses only itself.
    if target_context is not None and target_context not in {context, SHARED}:
        return False
    if context == SHARED and target_context not in {SHARED, None}:
        return False
    # Infrastructure reaches the application layer only through its context's ports.
    if layer == "infrastructure" and target_layer == "application":
        ports = f"data_pipeline.application.{context}.ports"
        return imported == ports or imported.startswith(f"{ports}.")
    return RANK[target_layer] <= RANK[layer]


def all_modules() -> list[tuple[str, Path]]:
    return [(module_name(path), path) for path in sorted(PACKAGE.rglob("*.py"))]


def is_stray(module: str) -> bool:
    """A module that is not inside ``<layer>.<context>``, an entry point, or bootstrap."""
    depth = module.count(".")
    layer, context = placement(module)
    if depth == 0:
        return False
    if depth == 1:
        return layer not in {*LAYERS, *OUTER, BOOTSTRAP}
    if layer in OUTER:
        return False
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


def file_calls(path: Path) -> set[str]:
    calls: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name in FILE_CALLS:
                calls.add(name)
    return calls


def test_domain_modules_touch_no_files() -> None:
    violations = [
        f"{module} calls {call}"
        for module, path in all_modules()
        if placement(module)[0] == "domain"
        for call in sorted(file_calls(path))
    ]
    assert violations == []


def test_every_module_sits_in_a_layer_and_a_context() -> None:
    assert [module for module, _ in all_modules() if is_stray(module)] == []


def test_a_module_outside_the_layers_is_a_stray() -> None:
    for module in (
        "data_pipeline.helpers",
        "data_pipeline.domain.helpers",
        "data_pipeline.domain.legacy.models",
        "data_pipeline.presentation.cli.main",
    ):
        assert is_stray(module), module
    for module in (
        "data_pipeline",
        "data_pipeline.bootstrap",
        "data_pipeline.domain",
        "data_pipeline.domain.mediator",
        "data_pipeline.infrastructure.shared.files",
        "data_pipeline.cli.main",
    ):
        assert not is_stray(module), module


def test_entry_points_do_not_build_adapters() -> None:
    assert not allowed(
        "data_pipeline.cli.reextract", "data_pipeline.infrastructure.mediator.sqlite_queue"
    )
    assert allowed("data_pipeline.cli.reextract", "data_pipeline.bootstrap")
    assert allowed("data_pipeline.cli.reextract", "data_pipeline.application.mediator.add_urls")
    assert allowed("data_pipeline.bootstrap", "data_pipeline.infrastructure.mediator.sqlite_queue")
    assert not allowed("data_pipeline.application.mediator.add_urls", "data_pipeline.bootstrap")
    # The composition root serves the entry points; it never reaches back into them.
    assert not allowed("data_pipeline.bootstrap", "data_pipeline.cli.context")


def test_layers_point_inwards() -> None:
    assert allowed(
        "data_pipeline.application.mediator.dispatch", "data_pipeline.domain.mediator.rotation"
    )
    assert not allowed(
        "data_pipeline.domain.mediator.rotation", "data_pipeline.application.mediator.dispatch"
    )
    assert not allowed(
        "data_pipeline.application.reader.parse", "data_pipeline.infrastructure.reader.cache"
    )


def test_infrastructure_may_import_only_ports_from_application() -> None:
    infra = "data_pipeline.infrastructure.reader.cache"
    assert allowed(infra, "data_pipeline.application.reader.ports")
    assert not allowed(infra, "data_pipeline.application.reader.parse")
    assert not allowed(infra, "data_pipeline.application.downloader.ports")
    # Only the ports module itself, not a sibling that happens to share the prefix.
    assert not allowed(infra, "data_pipeline.application.reader.ports_extra")


def test_contexts_never_import_each_other() -> None:
    assert not allowed(
        "data_pipeline.application.corpus.package", "data_pipeline.domain.reader.models"
    )
    assert allowed("data_pipeline.application.corpus.package", "data_pipeline.domain.shared.url")
    assert not allowed("data_pipeline.domain.shared.url", "data_pipeline.domain.downloader.access")
    assert allowed(
        "data_pipeline.bootstrap",
        "data_pipeline.infrastructure.mediator.sqlite_queue",
    )


def test_only_infrastructure_uses_shared_infrastructure() -> None:
    files = "data_pipeline.infrastructure.shared.files"
    assert not allowed("data_pipeline.domain.reader.models", files)
    assert not allowed("data_pipeline.application.reader.parse", files)
    assert allowed("data_pipeline.infrastructure.reader.cache", files)


def test_blocks_do_not_import_each_other() -> None:
    assert not allowed(
        "data_pipeline.application.downloader.fetch", "data_pipeline.domain.reader.records"
    )
    assert not allowed(
        "data_pipeline.infrastructure.merger.outbox",
        "data_pipeline.infrastructure.storage.mongo",
    )
    assert allowed(
        "data_pipeline.application.downloader.fetch", "data_pipeline.domain.shared.protocol"
    )


def test_runner_may_wire_blocks() -> None:
    assert allowed("data_pipeline.runner.adapters", "data_pipeline.application.reader.parse")
    assert allowed("data_pipeline.runner.adapters", "data_pipeline.application.storage.store")
    assert not allowed(
        "data_pipeline.runner.adapters", "data_pipeline.infrastructure.storage.mongo"
    )
    assert not allowed("data_pipeline.runner.crawl", "data_pipeline.cli.crawl")
    assert allowed("data_pipeline.cli.crawl", "data_pipeline.runner.crawl")
    assert not allowed("data_pipeline.application.reader.parse", "data_pipeline.runner.adapters")
