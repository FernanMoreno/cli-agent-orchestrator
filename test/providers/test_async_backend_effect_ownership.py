"""Direct async backend effects must use the common storage-aware worker."""

import ast
from pathlib import Path

import pytest

from cli_agent_orchestrator.backends.work_backend import WorkBackendView

pytestmark = pytest.mark.offline_database
PROVIDERS = Path(__file__).resolve().parents[2] / "src/cli_agent_orchestrator/providers"


class DirectEffects(ast.NodeVisitor):
    def __init__(self):
        self.function = None
        self.calls = []
        self.aliases = set()

    def visit_FunctionDef(self, node):
        # Sync helpers may be handed to a joined worker; this rule covers direct calls.
        return

    def visit_Lambda(self, node):
        return

    def visit_AsyncFunctionDef(self, node):
        previous, aliases = self.function, self.aliases
        self.function, self.aliases = node.name, set()
        for statement in node.body:
            self.visit(statement)
        self.function, self.aliases = previous, aliases

    def visit_Assign(self, node):
        if (
            self.function is not None
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "get_backend"
        ):
            self.aliases.update(
                target.id for target in node.targets if isinstance(target, ast.Name)
            )
        self.generic_visit(node)

    def visit_Call(self, node):
        function = node.func
        if (
            self.function is not None
            and isinstance(function, ast.Attribute)
            and function.attr in WorkBackendView._EFFECTS
            and (
                (
                    isinstance(function.value, ast.Call)
                    and isinstance(function.value.func, ast.Name)
                    and function.value.func.id == "get_backend"
                )
                or (isinstance(function.value, ast.Name) and function.value.id in self.aliases)
            )
        ):
            self.calls.append((self.function, node.lineno, function.attr))
        self.generic_visit(node)


@pytest.mark.parametrize("path", sorted(PROVIDERS.glob("*.py")), ids=lambda path: path.stem)
def test_async_provider_has_no_direct_blocking_backend_effect(path):
    scan = DirectEffects()
    scan.visit(ast.parse(path.read_text()))
    assert not scan.calls, f"Route these effects through BaseProvider._run_blocking: {scan.calls}"
