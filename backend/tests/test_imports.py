"""Every module in the application package must import cleanly.

This is the cheapest possible guard against the failure modes a large package
move introduces: a stale relative import, a wrong relative depth, a typo in a
new module path, or an import cycle. Any of those raise here rather than at
runtime inside a container.

Run:  uv run python -m pytest tests/ -q
  or: uv run python tests/test_imports.py
"""

from __future__ import annotations

import importlib
import pkgutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app  # noqa: E402


def _walk() -> list[str]:
    return [m.name for m in pkgutil.walk_packages(app.__path__, "app.")]


def test_every_module_imports() -> None:
    failures: list[tuple[str, str]] = []
    for name in _walk():
        try:
            importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001 - we want every failure, not the first
            failures.append((name, f"{type(exc).__name__}: {exc}"))
    assert not failures, "modules failed to import:\n" + "\n".join(
        f"  {name}: {err}" for name, err in failures
    )


def test_entrypoints_resolve() -> None:
    """The three things Docker, Compose and the CLI actually invoke."""
    importlib.import_module("app.main")
    importlib.import_module("app.pipeline.consumer")
    importlib.import_module("app.cli")


def test_no_undefined_names() -> None:
    """No module may reference a name it never imports or defines.

    Importing a module does not execute its function bodies, so a name used only
    inside a function — ``quote`` in a header helper, say — survives an import
    test and fails on the first request that reaches it. Splitting one large
    module into several is exactly the operation that drops such an import, so
    this walks the AST instead of trusting the import.
    """
    import ast
    import builtins

    root = Path(__file__).resolve().parent.parent / "app"
    known = set(dir(builtins)) | {"__name__", "__file__", "annotations"}
    offenders: list[str] = []
    for path in sorted(root.rglob("*.py")):
        defined: set[str] = set()
        used: set[str] = set()
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    defined.add((alias.asname or alias.name).split(".")[0])
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                defined.add(node.name)
            elif isinstance(node, ast.arg):
                defined.add(node.arg)
            elif isinstance(node, ast.Name):
                (defined if isinstance(node.ctx, ast.Store) else used).add(node.id)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                defined.add(node.name)
        missing = sorted(used - defined - known)
        if missing:
            offenders.append(f"  {path.relative_to(root.parent)}: {missing}")
    assert not offenders, "names used but never imported or defined:\n" + "\n".join(offenders)


if __name__ == "__main__":
    test_every_module_imports()
    test_entrypoints_resolve()
    test_no_undefined_names()
    print(f"OK — {len(_walk())} modules import, entrypoints resolve, no undefined names")
