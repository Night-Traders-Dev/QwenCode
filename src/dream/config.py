"""
dream/config.py — Model identifiers, endpoint config, and cycle parameters.
Adjust model names to match your Ollama pull names exactly.
"""

import os
from dataclasses import dataclass, field
from typing import Optional

from config.config import (
    DASHSCOPE_BASE_URL,
    DEFAULT_MODEL,
    LOCAL_API_KEY,
    LOCAL_BASE_URL,
    CLAUDE_OPUS_MODEL,
    load_config,
)


@dataclass
class ModelConfig:
    name: str
    role: str
    base_url: str = "http://localhost:11434"
    api_key: Optional[str] = None
    temperature: float = 0.7
    max_tokens: int = 2048
    context_window: int = 8192
    timeout: float = 120.0


@dataclass
class DreamConfig:
    cloud: ModelConfig = field(default_factory=lambda: ModelConfig(
        name=DEFAULT_MODEL,
        role="cloud-orchestrator",
        base_url=DASHSCOPE_BASE_URL,
        api_key=os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("OPENAI_API_KEY"),
        temperature=0.6,
        max_tokens=4096,
        context_window=32768,
        timeout=300.0,
    ))
    medium: ModelConfig = field(default_factory=lambda: ModelConfig(
        # medium must remain a meaningfully stronger or at least distinct model
        # from small, otherwise the student/verifier split collapses
        name=CLAUDE_OPUS_MODEL,
        role="medium-student",
        base_url=LOCAL_BASE_URL,
        api_key=LOCAL_API_KEY,
        temperature=0.7,
        max_tokens=4096,
        context_window=12288,
        timeout=120.0,
    ))
    small: ModelConfig = field(default_factory=lambda: ModelConfig(
        # small should be a distinct lightweight verifier/grader model
        name="qwen3.5:0.8b",
        role="small-verifier-grader",
        base_url=LOCAL_BASE_URL,
        api_key=LOCAL_API_KEY,
        temperature=0.3,
        max_tokens=2048,
        context_window=8192,
        timeout=120.0,
    ))

    gather_queries_per_model: int = 20
    questions_per_test: int = 100
    min_verify_confidence: float = 0.7
    passing_score: float = 0.80
    max_topic_retries: int = 3

    target_duration_hours: float = 4.0
    checkpoint_every_n_cycles: int = 10
    memory_path: str = "dream_memory.json"
    log_path: str = "dream.log"
    resume_existing: bool = False
    live_ui: bool = True
    session_id: str = field(default_factory=lambda: os.environ.get("DREAM_SESSION_ID", "dream"))
    memory_backend: str = field(default_factory=lambda: load_config().get("memory_backend", "auto"))
    memory_db_url: Optional[str] = field(default_factory=lambda: load_config().get("memory_db_url") or None)
    require_postgres: bool = field(default_factory=lambda: bool(load_config().get("require_postgres", False)))

    research_enabled: bool = True
    research_max_sources: int = 15
    research_statement_limit: int = 15
    research_chars_per_source: int = 1500
    research_max_context_chars: int = 12000
    research_timeout_seconds: float = 30.0
    research_refresh_seconds: float = 900.0

    local_inference_cooldown: float = 1.0