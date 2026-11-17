"""The never-wired Phase-2 stubs (ai_engine.ai_gateway, ai_engine.semantic_router, ai_workers.multimodal) are gone, and nothing in the code base still points at them."""
import ast
import importlib
import pathlib
import unittest

SRC = pathlib.Path(__file__).resolve().parents[1]
REMOVED = ("src.ai_engine.ai_gateway", "src.ai_engine.semantic_router", "src.ai_workers.multimodal")


class RemovedStubsTests(unittest.TestCase):
    def test_the_real_import_paths_still_load(self):
        for name in ("src.ai_engine", "src.ai_engine.schemas", "src.ai_engine.company_context", "src.ai_engine.llm_orchestrator", "src.ai_workers.llm_invoker.worker",
                     "src.gateway.main"):
            importlib.import_module(name)
        import src.ai_engine as engine
        self.assertEqual(sorted(engine.__all__), ["LLMOrchestrator", "RoutingDecision", "RoutingRequest"])

    def test_the_removed_modules_are_not_importable(self):
        for name in REMOVED:
            with self.assertRaises(ModuleNotFoundError, msg=name):
                importlib.import_module(name)

    def test_no_python_file_imports_a_removed_module_or_name(self):
        bad = []
        for path in list(SRC.glob("src/**/*.py")) + list(SRC.glob("scripts/**/*.py")) + list(SRC.glob("tests/**/*.py")) + list(SRC.glob("alembic/**/*.py")):
            if path.name == pathlib.Path(__file__).name:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                mods = []
                if isinstance(node, ast.Import):
                    mods = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    base = node.module or ""
                    mods = [base] + [f"{base}.{a.name}" for a in node.names]
                    if base == "src.ai_engine" and any(a.name in ("AIGateway", "SemanticRouter", "PreLLMCheckResult", "PostLLMCheckResult") for a in node.names):
                        bad.append((str(path.relative_to(SRC)), "removed name from src.ai_engine"))
                if any(m == r or m.startswith(r + ".") for m in mods for r in REMOVED):
                    bad.append((str(path.relative_to(SRC)), mods))
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
