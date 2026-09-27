"""Unit tests for AstAnalyzer."""
import unittest
from agent_trace.engine.ast_analyzer import AstAnalyzer


class TestExtractSymbols(unittest.TestCase):
    def setUp(self):
        self.az = AstAnalyzer()

    def test_extract_symbols_python(self):
        code = (
            "class Foo:\n"
            "    def bar(self):\n"
            "        pass\n"
            "    def baz(self):\n"
            "        pass\n"
        )
        syms = self.az.extract_symbols_from_source(code, "module.py")
        names = [s["name"] for s in syms]
        types = {s["name"]: s["type"] for s in syms}
        self.assertIn("Foo", names)
        self.assertIn("bar", names)
        self.assertIn("baz", names)
        self.assertEqual(types["Foo"], "CLASS")
        self.assertEqual(types["bar"], "FUNCTION")
        self.assertEqual(types["baz"], "FUNCTION")

    def test_extract_symbols_fallback_js(self):
        code = (
            "class MyService {\n"
            "    doWork() {\n"
            "        return 1;\n"
            "    }\n"
            "}\n"
            "function helperFn() {}\n"
        )
        syms = self.az.extract_symbols_from_source(code, "service.ts")
        names = [s["name"] for s in syms]
        types = {s["name"]: s["type"] for s in syms}
        self.assertIn("MyService", names)
        self.assertEqual(types["MyService"], "CLASS")
        self.assertIn("helperFn", names)
        self.assertEqual(types["helperFn"], "FUNCTION")


class TestFindEnclosingSymbol(unittest.TestCase):
    def setUp(self):
        self.az = AstAnalyzer()
        self.symbols = [
            {"name": "Foo", "type": "CLASS", "start_line": 1, "end_line": 20, "file_path": "f.py"},
            {"name": "bar", "type": "FUNCTION", "start_line": 2, "end_line": 10, "file_path": "f.py"},
            {"name": "baz", "type": "FUNCTION", "start_line": 12, "end_line": 18, "file_path": "f.py"},
        ]

    def test_find_enclosing_symbol_found(self):
        result = self.az.find_enclosing_symbol(self.symbols, 5)
        # Line 5 is inside bar (2–10); reversed iteration picks bar before Foo
        self.assertEqual(result, "bar")

    def test_find_enclosing_symbol_not_found(self):
        result = self.az.find_enclosing_symbol(self.symbols, 99)
        self.assertIsNone(result)


class TestSemanticDiff(unittest.TestCase):
    def setUp(self):
        self.az = AstAnalyzer()

    def test_semantic_diff_added(self):
        before = "def a():\n    pass\n"
        after = "def a():\n    pass\ndef b():\n    pass\n"
        diff = self.az.semantic_diff(before, after, "f.py")
        self.assertIn("b", diff["added"])
        self.assertNotIn("b", diff["removed"])

    def test_semantic_diff_removed(self):
        before = "def a():\n    pass\ndef b():\n    pass\n"
        after = "def a():\n    pass\n"
        diff = self.az.semantic_diff(before, after, "f.py")
        self.assertIn("b", diff["removed"])
        self.assertNotIn("b", diff["added"])

    def test_semantic_diff_no_change(self):
        source = "def a():\n    pass\n"
        diff = self.az.semantic_diff(source, source, "f.py")
        self.assertEqual(diff["added"], [])
        self.assertEqual(diff["removed"], [])


if __name__ == "__main__":
    unittest.main()
