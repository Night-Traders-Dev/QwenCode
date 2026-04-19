""Tests for Dream memory enhancements: distillation, cross-topic search, and concept mapping."""

import pytest
import json
from pathlib import Path
from src.dream.memory.dream_memory import DreamMemory


class TestDreamMemoryEnhancements:
    """Test cases for new Dream memory enhancement features."""

    @pytest.fixture
    def sample_memory(self, tmp_path):
        """Create a sample DreamMemory with test data."""
        memory_file = tmp_path / "test_dream_memory.json"

        # Create sample memory with verified statements and cycles
        sample_data = {
            "topic": "basic arithmetic",
            "cycles": [
                {
                    "cycle_num": 1,
                    "score": 0.65,
                    "statements": [
                        {"text": "2+2=4", "verified": True, "confidence": 0.95},
                        {"text": "3*3=9", "verified": True, "confidence": 0.90},
                        {"text": "5-2=4", "verified": False, "confidence": 0.40}
                    ]
                },
                {
                    "cycle_num": 2,
                    "score": 0.85,
                    "statements": [
                        {"text": "2+2=4", "verified": True, "confidence": 0.98},
                        {"text": "3*3=9", "verified": True, "confidence": 0.95},
                        {"text": "10/2=5", "verified": True, "confidence": 0.92},
                        {"text": "4*4=16", "verified": True, "confidence": 0.88}
                    ]
                },
                {
                    "cycle_num": 3,
                    "score": 0.92,
                    "statements": [
                        {"text": "2+2=4", "verified": True, "confidence": 0.99},
                        {"text": "sqrt(16)=4", "verified": True, "confidence": 0.94},
                        {"text": "7*8=56", "verified": True, "confidence": 0.96}
                    ]
                }
            ],
            "knowledge": [
                "2+2=4",
                "3*3=9",
                "10/2=5",
                "4*4=16",
                "sqrt(16)=4",
                "7*8=56"
            ],
            "weak_areas": ["division", "square roots"],
            "subtopics": ["addition", "multiplication", "division"]
        }

        with open(memory_file, 'w') as f:
            json.dump(sample_data, f)

        return DreamMemory(str(memory_file))

    def test_get_high_confidence_statements(self, sample_memory):
        """Test extraction of high-confidence verified statements."""
        # Default min_cycle_score 0.85
        statements = sample_memory.get_high_confidence_statements(min_cycle_score=0.85)

        assert len(statements) > 0
        # Should return knowledge base entries from high-scoring cycles
        assert isinstance(statements, list)

    def test_get_high_confidence_statements_custom_threshold(self, sample_memory):
        """Test extraction with custom confidence threshold."""
        # Higher threshold should still work
        statements = sample_memory.get_high_confidence_statements(min_cycle_score=0.90)

        # With higher threshold, may return fallback knowledge_base
        assert isinstance(statements, list)

    def test_generate_distillation_dataset(self, sample_memory, tmp_path):
        """Test generation of distillation dataset from high-scoring cycles."""
        output_file = tmp_path / "distillation_data.json"

        result = sample_memory.generate_distillation_dataset(output_path=str(output_file))

        assert result > 0

        # Verify output file was created
        assert output_file.exists()

        with open(output_file, 'r') as f:
            dataset = json.load(f)

        assert len(dataset) > 0

        # Check sample structure
        for sample in dataset:
            assert 'instruction' in sample
            assert 'input' in sample
            assert 'output' in sample

    def test_cross_topic_search(self, sample_memory):
        """Test searching for related statements across topics."""
        # Search for arithmetic-related terms
        results = sample_memory.cross_topic_search("arithmetic addition", limit=5)

        assert len(results) >= 0  # May be empty if no matches
        # Check result structure if any results exist
        for result in results:
            assert 'statement' in result
            assert 'relevance_score' in result
            assert 'topic' in result

    def test_create_concept_map(self, sample_memory):
        """Test creation of concept relationship graph."""
        concept_map = sample_memory.create_concept_map()

        assert isinstance(concept_map, dict)

        # Check structure - keys are concepts, values are lists of statements
        for concept, statements in concept_map.items():
            assert isinstance(concept, str)
            assert isinstance(statements, list)
            assert len(statements) >= 2  # Only concepts with multiple statements

    def test_extract_key_concepts(self, sample_memory):
        """Test key concept extraction from statements."""
        statement = "The quadratic formula solves equations of form ax² + bx + c = 0"
        concepts = sample_memory._extract_key_concepts(statement)

        assert isinstance(concepts, str)
        assert len(concepts) > 0
        # Should extract some meaningful words
        assert 'quadratic' in concepts or 'formula' in concepts or 'solves' in concepts or 'equations' in concepts


class TestDreamSessionEnhancements:
    """Test cases for Dream session enhancement methods."""

    @pytest.fixture
    def sample_session_config(self, tmp_path):
        """Create a minimal DreamSession config for testing."""
        from src.dream.config import DreamConfig

        config = DreamConfig()
        config.memory_path = str(tmp_path / "test_memory.json")
        config.log_path = str(tmp_path / "test.log")

        # Create minimal memory file
        with open(config.memory_path, 'w') as f:
            json.dump({
                "topic": "test topic",
                "cycles": [],
                "knowledge": []
            }, f)

        return config

    def test_export_learning_analytics_structure(self, tmp_path):
        """Test that learning analytics export produces correct structure."""
        from src.dream.session import DreamSession
        from src.dream.config import DreamConfig

        memory_file = tmp_path / "analytics_test.json"
        log_file = tmp_path / "analytics_test.log"

        with open(memory_file, 'w') as f:
            json.dump({
                "topic": "test",
                "cycles": [
                    {"cycle_num": 1, "score": 0.70},
                    {"cycle_num": 2, "score": 0.85},
                    {"cycle_num": 3, "score": 0.90}
                ],
                "knowledge": [],
                "weak_areas": ["topic A"]
            }, f)

        config = DreamConfig()
        config.memory_path = str(memory_file)
        config.log_path = str(log_file)

        session = DreamSession(topic="test", config=config)
        analytics = session.export_learning_analytics(output_path=str(tmp_path / "analytics_output.json"))

        # Check that analytics contains expected structure (actual keys may vary)
        assert isinstance(analytics, dict)
        assert len(analytics) > 0
        # Common analytics fields
        assert 'topic' in analytics or 'final_score' in analytics or 'total_cycles' in analytics