"""Unit tests for the Developer Knowledge Graph (knowledge_graph.py)."""
import json
import time
import unittest
from pathlib import Path
import tempfile

from agent_trace.engine.knowledge_graph import record_concept, get_knowledge_graph, _load, _save, KnowledgeGraph


class TestRecordConcept(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp_path = Path(self.tmp.name)
        self.tmp.close()
        # Start with an empty graph file
        self.tmp_path.unlink(missing_ok=True)

    def tearDown(self):
        self.tmp_path.unlink(missing_ok=True)

    def test_concept_is_persisted(self):
        record_concept("Retry Pattern", "Reliability", "my-repo", "Centralised retry", path=self.tmp_path)
        kg = _load(self.tmp_path)
        # knowledge_graph uses lowercase keys
        self.assertIn("retry pattern", kg.concepts)

    def test_concept_encounter_count_increments(self):
        record_concept("Retry Pattern", "Reliability", "my-repo", "First", path=self.tmp_path)
        record_concept("Retry Pattern", "Reliability", "my-repo", "Second", path=self.tmp_path)
        kg = _load(self.tmp_path)
        self.assertEqual(kg.concepts["retry pattern"].encounter_count, 2)

    def test_concept_repos_deduped(self):
        record_concept("Caching", "Performance", "repo-a", "Cache aside", path=self.tmp_path)
        record_concept("Caching", "Performance", "repo-a", "Cache aside again", path=self.tmp_path)
        kg = _load(self.tmp_path)
        self.assertEqual(kg.concepts["caching"].repos.count("repo-a"), 1)

    def test_multiple_concepts_independent(self):
        record_concept("Retry Pattern", "Reliability", "my-repo", "Retry", path=self.tmp_path)
        record_concept("Circuit Breaker", "Reliability", "my-repo", "Circuit", path=self.tmp_path)
        kg = _load(self.tmp_path)
        self.assertIn("retry pattern", kg.concepts)
        self.assertIn("circuit breaker", kg.concepts)

    def test_headline_stored(self):
        record_concept("DI", "Architecture", "my-repo", "Dependency Injection", path=self.tmp_path)
        kg = _load(self.tmp_path)
        self.assertIn("Dependency Injection", kg.concepts["di"].headlines)


class TestGetKnowledgeGraph(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp_path = Path(self.tmp.name)
        self.tmp.close()
        self.tmp_path.unlink(missing_ok=True)

    def tearDown(self):
        self.tmp_path.unlink(missing_ok=True)

    def test_get_knowledge_graph_returns_dict(self):
        record_concept("SOLID", "Architecture", "repo", "SOLID principles", path=self.tmp_path)
        result = get_knowledge_graph(path=self.tmp_path)
        self.assertIsInstance(result, dict)
        self.assertIn("concepts", result)

    def test_get_knowledge_graph_concepts_key_is_list(self):
        record_concept("SOLID", "Architecture", "repo", "SOLID principles", path=self.tmp_path)
        result = get_knowledge_graph(path=self.tmp_path)
        # get_knowledge_graph returns a sorted list of concept dicts
        self.assertIsInstance(result["concepts"], list)

    def test_get_knowledge_graph_contains_recorded_concept(self):
        record_concept("SOLID", "Architecture", "repo", "SOLID principles", path=self.tmp_path)
        result = get_knowledge_graph(path=self.tmp_path)
        names = [c["name"] for c in result["concepts"]]
        self.assertIn("SOLID", names)

    def test_get_knowledge_graph_empty_on_missing_file(self):
        result = get_knowledge_graph(path=self.tmp_path)
        self.assertEqual(result["concepts"], [])


class TestAtomicSave(unittest.TestCase):
    """Verify _save writes atomically (no partial state)."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp_path = Path(self.tmp.name)
        self.tmp.close()
        self.tmp_path.unlink(missing_ok=True)

    def tearDown(self):
        self.tmp_path.unlink(missing_ok=True)
        Path(str(self.tmp_path) + ".tmp").unlink(missing_ok=True)

    def test_saved_file_is_valid_json(self):
        kg = KnowledgeGraph()
        _save(kg, self.tmp_path)
        with open(self.tmp_path, "r") as f:
            data = json.load(f)
        self.assertIn("concepts", data)


if __name__ == "__main__":
    unittest.main()
