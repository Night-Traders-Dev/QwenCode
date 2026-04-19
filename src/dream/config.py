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
    CLAUDE_OPUS_MODEL
    load_config,
)


@dataclass
class ModelConfig:
    name: str                        # Ollama model tag
    role: str                        # human-readable role label
    base_url: str = "http://localhost:11434"
    api_key: Optional[str] = None    # cloud key if needed
    temperature: float = 0.7
    max_tokens: int = 2048
    context_window: int = 8192
    timeout: float = 120.0           # seconds per inference


@dataclass
class DreamConfig:
    # ── Model definitions ──────────────────────────────────────────────────
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
        name=CLAUDE_OPUS_MODEL,  #"qwen3.5:4b",
        role="medium-student",
        base_url=LOCAL_BASE_URL,
        api_key=LOCAL_API_KEY,
        temperature=0.7,
        max_tokens=2048,
        context_window=8192,
        timeout=120.0,
    ))
    small: ModelConfig = field(default_factory=lambda: ModelConfig(
        name=CLAUDE_OPUS_MODEL,  # "qwen3.5:0.8b",
        role="small-verifier",
        base_url=LOCAL_BASE_URL,
        api_key=LOCAL_API_KEY,
        temperature=0.3,       # low temp for grading / verification
        max_tokens=1024,
        context_window=4096,
        timeout=60.0,
    ))

    # ── Cycle parameters ───────────────────────────────────────────────────
    gather_queries_per_model: int = 3   # queries each model fires per gather phase
    questions_per_test: int = 10        # questions the cloud generates per test
    min_verify_confidence: float = 0.6  # 0–1 threshold; below = data flagged
    passing_score: float = 0.70         # grade at which a topic is "learned"
    max_topic_retries: int = 5          # cycles before moving past a topic

    # ── Session parameters ─────────────────────────────────────────────────
    target_duration_hours: float = 4.0
    checkpoint_every_n_cycles: int = 5
    memory_path: str = "dream_memory.json"
    log_path: str = "dream.log"
    resume_existing: bool = False
    live_ui: bool = True
    session_id: str = field(default_factory=lambda: os.environ.get("DREAM_SESSION_ID", "dream"))
    memory_backend: str = field(default_factory=lambda: load_config().get("memory_backend", "auto"))
    memory_db_url: Optional[str] = field(default_factory=lambda: load_config().get("memory_db_url") or None)
    require_postgres: bool = field(default_factory=lambda: bool(load_config().get("require_postgres", False)))

    # ── Internet research ──────────────────────────────────────────────────
    research_enabled: bool = True
    research_max_sources: int = 4
    research_statement_limit: int = 8
    research_chars_per_source: int = 700
    research_max_context_chars: int = 2400
    research_timeout_seconds: float = 15.0
    research_refresh_seconds: float = 900.0

    # ── VRAM guard ─────────────────────────────────────────────────────────
    # Seconds to wait between local model calls to avoid OOM on 8GB 5060
    local_inference_cooldown: float = 1.0
