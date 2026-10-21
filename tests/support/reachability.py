"""Import-resolved call graph, used to prove route handlers cannot reach fixture readers.

Unlike a name-based search, calls are resolved through imports and re-export chains, so a method that merely
shares a name with a fixture loader (``repo.get_reference()``) is not a false positive, while a loader
imported under another name, imported inside a function, or passed as a callback is still found. Calls on
objects and dynamic dispatch are not followed; that limit is documented where the gate is used.
"""

import ast
import collections
from dataclasses import dataclass, field
from pathlib import Path

FIXTURE_MODULE_BASENAMES = ("fixture_repository", "sample_loader", "fixture_provider")
FIXTURE_FUNCTION_PREFIXES = ("get_sample_", "get_demo_", "predict_demo_")
RESEARCH_ENTRY_NAMES = ("run_benchmark_batch", "evaluate_workflow_by_id", "get_workflow_by_id", "list_benchmark_workflows")
ROUTE_PACKAGES = ("app.api", "app.web", "extension.cluster")

Symbol = tuple[str, str]  # (module, function name)


@dataclass
class ModuleInfo:
    name: str
    tree: ast.Module
    functions: dict[str, ast.AST] = field(default_factory=dict)
    imports: dict[str, tuple[str, str | None]] = field(default_factory=dict)  # alias -> (module, symbol | None)
    imported_modules: set[str] = field(default_factory=set)


def _dotted(node: ast.AST) -> str | None:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


class CodeIndex:
    def __init__(self, modules: dict[str, ModuleInfo]) -> None:
        self.modules = modules
        for info in modules.values():
            self._collect_imports(info)

    @classmethod
    def build(cls, root: Path, packages: tuple[str, ...]) -> "CodeIndex":
        modules: dict[str, ModuleInfo] = {}
        for package in packages:
            for path in sorted((root / package).rglob("*.py")):
                if "__pycache__" in path.parts:
                    continue
                name = ".".join(path.relative_to(root).with_suffix("").parts)
                if name.endswith(".__init__"):
                    name = name[: -len(".__init__")]
                info = ModuleInfo(name=name, tree=ast.parse(path.read_text(encoding="utf-8")))
                for node in info.tree.body:
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        info.functions[node.name] = node
                modules[name] = info
        return cls(modules)

    # -- imports ---------------------------------------------------------------------------------------------

    def _collect_imports(self, info: ModuleInfo) -> None:
        package = info.name.rsplit(".", 1)[0] if "." in info.name else ""
        for node in ast.walk(info.tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    info.imported_modules.add(alias.name)
                    if alias.asname:
                        info.imports[alias.asname] = (alias.name, None)
                    else:
                        info.imports[alias.name.split(".")[0]] = (alias.name.split(".")[0], None)
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    anchor = package.split(".")
                    anchor = anchor[: len(anchor) - (node.level - 1)] if node.level > 1 else anchor
                    base = ".".join(filter(None, [".".join(anchor), base]))
                info.imported_modules.add(base)
                for alias in node.names:
                    target = f"{base}.{alias.name}"
                    if target in self.modules:
                        info.imported_modules.add(target)
                        info.imports[alias.asname or alias.name] = (target, None)
                    else:
                        info.imports[alias.asname or alias.name] = (base, alias.name)

    def imports_any(self, module: str, targets: tuple[str, ...]) -> bool:
        return bool(self.modules[module].imported_modules & set(targets))

    # -- resolution ------------------------------------------------------------------------------------------

    def resolve_symbol(self, module: str, name: str, seen: frozenset[Symbol] = frozenset()) -> Symbol | None:
        if (module, name) in seen or module not in self.modules:
            return None
        info = self.modules[module]
        if name in info.functions:
            return module, name
        imported = info.imports.get(name)
        if imported and imported[1] is not None:
            return self.resolve_symbol(imported[0], imported[1], seen | {(module, name)})
        return None

    def _callees(self, symbol: Symbol) -> set[Symbol]:
        module, name = symbol
        info = self.modules[module]
        found: set[Symbol] = set()
        for node in ast.walk(info.functions[name]):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                local = self.resolve_symbol(module, node.id)
                if local:
                    found.add(local)
            elif isinstance(node, ast.Attribute):
                dotted = _dotted(node)
                if not dotted or "." not in dotted:
                    continue
                head, _, rest = dotted.partition(".")
                imported = info.imports.get(head)
                if imported and imported[1] is None:
                    # module alias: try the longest module prefix, then resolve the function name
                    parts = [imported[0], *rest.split(".")]
                    for cut in range(len(parts) - 1, 0, -1):
                        candidate = ".".join(parts[:cut])
                        if candidate in self.modules:
                            resolved = self.resolve_symbol(candidate, parts[cut])
                            if resolved:
                                found.add(resolved)
                            break
        found.discard(symbol)
        return found

    def reachable(self, entries: list[Symbol], sinks: set[Symbol]) -> dict[Symbol, list[str]]:
        """For each entry that can reach a sink, the call path to the first sink found."""
        result: dict[Symbol, list[str]] = {}
        for entry in entries:
            seen = {entry}
            queue = collections.deque([(entry, [f"{entry[0]}.{entry[1]}"])])
            while queue:
                current, path = queue.popleft()
                if current in sinks and current != entry:
                    result[entry] = path
                    break
                for nxt in sorted(self._callees(current)):
                    if nxt not in seen:
                        seen.add(nxt)
                        queue.append((nxt, path + [f"{nxt[0]}.{nxt[1]}"]))
        return result


def route_entries(index: CodeIndex) -> list[Symbol]:
    entries = []
    for module, info in index.modules.items():
        if not module.startswith(ROUTE_PACKAGES):
            continue
        for name, node in info.functions.items():
            for decorator in getattr(node, "decorator_list", []):
                if (
                    isinstance(decorator, ast.Call)
                    and isinstance(decorator.func, ast.Attribute)
                    and isinstance(decorator.func.value, ast.Name)
                    and decorator.func.value.id == "router"
                ):
                    entries.append((module, name))
    return entries


def fixture_sinks(index: CodeIndex) -> set[Symbol]:
    sinks: set[Symbol] = set()
    for module, info in index.modules.items():
        in_fixture_module = module.rsplit(".", 1)[-1] in FIXTURE_MODULE_BASENAMES
        for name in info.functions:
            if in_fixture_module or name.startswith(FIXTURE_FUNCTION_PREFIXES) or name in RESEARCH_ENTRY_NAMES:
                sinks.add((module, name))
    return sinks
