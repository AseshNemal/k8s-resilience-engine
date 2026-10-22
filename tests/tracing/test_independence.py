"""The tracing infrastructure is shared and read-only: no engine import, no shared schema, no writes, no seeding."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FILES = sorted((ROOT / "app" / "integrations" / "tracing").glob("*.py")) + [ROOT / "extension" / "tracing_routes.py"]
FORBIDDEN_IMPORTS = ("app.engine", "app.shared", "app.api", "tests", "experiments")
WRITE_CALLS = {"post", "put", "patch", "delete", "request", "send"}
FORBIDDEN_WORDS = ("seed", "sample", "demo", "fixture", "fake")


def parsed(path):
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_the_files_exist():
    assert len(FILES) >= 5 and all(path.exists() for path in FILES)


def test_nothing_imports_an_engine_a_shared_schema_or_test_data():
    for path in FILES:
        for node in ast.walk(parsed(path)):
            names = [alias.name for alias in node.names] if isinstance(node, ast.Import) else [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            for name in names:
                assert not name.startswith(FORBIDDEN_IMPORTS), f"{path.name} imports {name}"


def test_nothing_can_send_data_to_the_trace_service():
    for path in FILES:
        for node in ast.walk(parsed(path)):
            if isinstance(node, ast.Attribute):
                assert node.attr not in WRITE_CALLS, f"{path.name} calls .{node.attr}"


def test_no_name_in_the_code_suggests_seeded_or_sample_data():
    for path in FILES:
        for node in ast.walk(parsed(path)):
            name = node.name if isinstance(node, (ast.FunctionDef, ast.ClassDef)) else node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else ""
            assert not any(word in name.lower() for word in FORBIDDEN_WORDS), f"{path.name}: {name}"


def test_the_routes_are_read_only():
    from extension.tracing_routes import router

    methods = {method for route in router.routes for method in route.methods}
    assert methods == {"GET"}
