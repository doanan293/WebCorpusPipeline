from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).parents[2]
SRC = ROOT / "src"
PACKAGE = SRC / "crawler"
# The package is split by layer first, then by context:
#   crawler.<layer>.<context>...,  crawler.api.<block>  and  crawler.cli...
LAYERS = ("domain", "application", "infra")
RANK = {layer: index for index, layer in enumerate(LAYERS)}
BLOCKS = frozenset({"mediator", "downloader", "reader", "merger", "storage"})
CONTEXTS = frozenset({"corpus", "hosts", *BLOCKS})
SHARED = "shared"
# The two entry points, side by side: api (HTTP, one module per block) and cli (commands).
# bootstrap.py is the composition root. cli.pipeline wires the blocks into one process for the
# commands, the one place allowed to use several blocks at once.
OUTER = frozenset({"api", "cli"})
BOOTSTRAP = "bootstrap"
PIPELINE = "crawler.cli.pipeline"
API_COMMON = "crawler.api.common"
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


def within(module: str, package: str) -> bool:
    return module == package or module.startswith(f"{package}.")


def api_allows(importer: str, imported: str) -> bool:
    """``crawler.api.<block>`` speaks for its own block only; api.common uses no block."""
    block = importer.split(".")[2] if importer.count(".") >= 2 else None
    target_layer, target_context = placement(imported)
    if target_layer == "cli":
        return False
    if target_layer == "api":
        return within(imported, API_COMMON) or within(imported, f"crawler.api.{block}")
    if target_layer in RANK:
        return target_context in {block, SHARED}
    return True


def allowed(importer: str, imported: str) -> bool:
    """Whether ``importer`` may import ``imported`` (both dotted module names)."""
    if not imported.startswith("crawler."):
        return True
    layer, context = placement(importer)
    target_layer, target_context = placement(imported)
    if layer == BOOTSTRAP:
        return target_layer not in OUTER
    # Entry points call use cases and bootstrap; they never build adapters themselves.
    if layer in OUTER:
        if target_layer == "infra":
            return False
        if layer == "api":
            return api_allows(importer, imported)
        # The pipeline serves the commands and never reaches back into them.
        if target_layer == "cli" and within(importer, PIPELINE):
            return within(imported, PIPELINE)
        return True
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
    if layer == "infra" and target_layer == "application":
        ports = f"crawler.application.{context}.ports"
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
        "crawler.helpers",
        "crawler.domain.helpers",
        "crawler.domain.legacy.models",
        "crawler.presentation.cli.main",
    ):
        assert is_stray(module), module
    for module in (
        "crawler",
        "crawler.bootstrap",
        "crawler.domain",
        "crawler.domain.mediator",
        "crawler.infra.shared.files",
        "crawler.cli.main",
        "crawler.api.reader",
    ):
        assert not is_stray(module), module


def test_entry_points_do_not_build_adapters() -> None:
    assert not allowed("crawler.cli.reextract", "crawler.infra.mediator.sqlite_queue")
    assert allowed("crawler.cli.reextract", "crawler.bootstrap")
    assert allowed("crawler.cli.reextract", "crawler.application.mediator.add_urls")
    assert allowed("crawler.bootstrap", "crawler.infra.mediator.sqlite_queue")
    assert not allowed("crawler.application.mediator.add_urls", "crawler.bootstrap")
    # The composition root serves the entry points; it never reaches back into them.
    assert not allowed("crawler.bootstrap", "crawler.cli.context")


def test_layers_point_inwards() -> None:
    assert allowed("crawler.application.mediator.dispatch", "crawler.domain.mediator.rotation")
    assert not allowed("crawler.domain.mediator.rotation", "crawler.application.mediator.dispatch")
    assert not allowed("crawler.application.reader.parse", "crawler.infra.reader.cache")


def test_infra_may_import_only_ports_from_application() -> None:
    infra = "crawler.infra.reader.cache"
    assert allowed(infra, "crawler.application.reader.ports")
    assert not allowed(infra, "crawler.application.reader.parse")
    assert not allowed(infra, "crawler.application.downloader.ports")
    # Only the ports module itself, not a sibling that happens to share the prefix.
    assert not allowed(infra, "crawler.application.reader.ports_extra")


def test_contexts_never_import_each_other() -> None:
    assert not allowed("crawler.application.corpus.package", "crawler.domain.reader.models")
    assert allowed("crawler.application.corpus.package", "crawler.domain.shared.url")
    assert not allowed("crawler.domain.shared.url", "crawler.domain.downloader.access")
    assert allowed(
        "crawler.bootstrap",
        "crawler.infra.mediator.sqlite_queue",
    )


def test_only_infra_uses_shared_infra() -> None:
    files = "crawler.infra.shared.files"
    assert not allowed("crawler.domain.reader.models", files)
    assert not allowed("crawler.application.reader.parse", files)
    assert allowed("crawler.infra.reader.cache", files)


def test_blocks_do_not_import_each_other() -> None:
    assert not allowed("crawler.application.downloader.fetch", "crawler.domain.reader.records")
    assert not allowed(
        "crawler.infra.merger.outbox",
        "crawler.infra.storage.mongo",
    )
    assert allowed("crawler.application.downloader.fetch", "crawler.domain.shared.protocol")


def test_pipeline_may_wire_blocks() -> None:
    assert allowed("crawler.cli.pipeline.adapters", "crawler.application.reader.parse")
    assert allowed("crawler.cli.pipeline.adapters", "crawler.application.storage.store")
    assert not allowed("crawler.cli.pipeline.adapters", "crawler.infra.storage.mongo")
    assert not allowed("crawler.cli.pipeline.crawl", "crawler.cli.crawl")
    assert allowed("crawler.cli.crawl", "crawler.cli.pipeline.crawl")
    assert not allowed("crawler.application.reader.parse", "crawler.cli.pipeline.adapters")


def test_api_module_uses_only_its_block() -> None:
    assert allowed("crawler.api.downloader", "crawler.application.downloader.fetch")
    assert allowed("crawler.api.downloader", "crawler.api.common")
    assert allowed("crawler.api.downloader", "crawler.domain.shared.protocol")
    assert allowed("crawler.api.downloader", "crawler.bootstrap")
    assert not allowed("crawler.api.downloader", "crawler.application.reader.parse")
    assert not allowed("crawler.api.downloader", "crawler.api.reader")
    assert not allowed("crawler.api.downloader", "crawler.infra.downloader.browser")
    assert not allowed("crawler.api.common", "crawler.application.reader.parse")


def test_entry_points_sit_side_by_side() -> None:
    # `crawler serve` starts the HTTP apps; the apps never call the commands.
    assert allowed("crawler.cli.serve", "crawler.api.reader")
    assert not allowed("crawler.api.reader", "crawler.cli.context")
    assert not allowed("crawler.bootstrap", "crawler.api.reader")
