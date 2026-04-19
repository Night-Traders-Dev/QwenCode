"""Tests for model display name shortening and configuration."""

import pytest
from src.config.config import get_model_display_name, CLAUDE_OPUS_MODEL, QWEN_SMALL_MODEL


class TestModelDisplayName:
    """Test cases for get_model_display_name function."""

    def test_hf_co_long_name_shortening(self):
        """Test that long hf.co URLs are shortened properly."""
        long_name = "hf.co/ermiaazarkhalili/LFM2.5-1.2B-SFT-Claude-Opus-Reasoning-Unsloth-GGUF:Q8_0"
        expected = "LFM2.5-1.2B (Q8_0)"
        assert get_model_display_name(long_name) == expected

    def test_simple_ollama_tag(self):
        """Test that simple Ollama tags remain unchanged."""
        assert get_model_display_name("qwen3.5:0.8b") == "qwen3.5:0.8b"
        assert get_model_display_name("qwen3.5:4b") == "qwen3.5:4b"

    def test_huggingface_standard_format(self):
        """Test HuggingFace standard author/model format."""
        assert get_model_display_name("Qwen/Qwen3.5-0.8B") == "Qwen3.5-0.8B"
        assert get_model_display_name("meta-llama/Llama-3-8B") == "Llama-3-8B"

    def test_empty_and_none_handling(self):
        """Test that empty strings return 'Unknown'."""
        assert get_model_display_name("") == "Unknown"
        assert get_model_display_name(None) == "Unknown"

    def test_quantization_variants(self):
        """Test various quantization suffixes."""
        assert get_model_display_name("hf.co/test/model-7B:Q4_K_M") == "model-7B (Q4_K_M)"
        assert get_model_display_name("hf.co/test/model-13B:Q8_0") == "model-13B (Q8_0)"

    def test_size_extraction_patterns(self):
        """Test size extraction from various naming patterns."""
        assert get_model_display_name("hf.co/test/LFM2.5-1.2B-test:Q8_0") == "LFM2.5-1.2B (Q8_0)"
        assert get_model_display_name("hf.co/test/model-0.6B:Q5_K_M") == "model-0.6B (Q5_K_M)"


class TestModelConstants:
    """Test that model constants are properly configured."""

    def test_claude_opus_model_constant(self):
        """Test CLAUDE_OPUS_MODEL constant is set correctly."""
        assert CLAUDE_OPUS_MODEL == "hf.co/ermiaazarkhalili/LFM2.5-1.2B-SFT-Claude-Opus-Reasoning-Unsloth-GGUF:Q8_0"

    def test_qwen_small_model_constant(self):
        """Test QWEN_SMALL_MODEL constant is set correctly."""
        assert QWEN_SMALL_MODEL == "qwen3.5:0.8b"

    def test_model_constants_display_names(self):
        """Test that model constants have proper display names."""
        assert get_model_display_name(CLAUDE_OPUS_MODEL) == "LFM2.5-1.2B (Q8_0)"
        assert get_model_display_name(QWEN_SMALL_MODEL) == "qwen3.5:0.8b"