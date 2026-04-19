"""
dream/agents/cloud.py — Cloud agent (Qwen3.6-35B-A3B).

Responsibilities:
  - Broad contextual knowledge gathering
  - Generating test questions + answer keys
  - Identifying subtopics and weak areas to probe next cycle
"""

import json
import logging
from typing import Any

from dream.agents.base import BaseAgent
from dream.config import ModelConfig

logger = logging.getLogger("dream.cloud")

_SYSTEM = """You are an expert teacher and knowledge orchestrator.
Your job is to gather accurate information about a topic, create rigorous test questions,
and produce precise answer keys. Always respond in the format explicitly requested.
Do not include any preamble or commentary outside the requested JSON structure."""


class CloudAgent(BaseAgent):

    def __init__(self, config: ModelConfig) -> None:
        super().__init__(config)

    @property
    def system_prompt(self) -> str:
        return _SYSTEM

    # ── Gather phase ───────────────────────────────────────────────────────

    async def gather(self, topic: str, subtopics: list[str], evidence: str = "") -> list[str]:
        """
        Produce a set of factual knowledge statements about `topic`.
        Returns a list of statement strings.
        """
        subtopic_str = ", ".join(subtopics) if subtopics else "general overview"
        evidence_block = f"\nReliable source notes:\n{evidence}\n" if evidence else "\nReliable source notes: none provided.\n"
        prompt = f"""Topic: {topic}
Focus areas: {subtopic_str}
{evidence_block}

Generate 5 precise, factual knowledge statements about this topic.
Each statement should be self-contained and verifiable.
Prefer statements that are directly supported by the reliable source notes when available.

Respond ONLY with a JSON array of strings, no other text.
Example: ["Statement one.", "Statement two.", ...]"""

        data = await self.generate_json(prompt)
        statements = self.coerce_string_list(data, "statements", "facts", "items")
        if statements:
            return statements
        logger.warning("[cloud] gather returned unexpected shape: %s", type(data))
        return []

    # ── Subtopic decomposition ─────────────────────────────────────────────

    async def decompose_topic(self, topic: str, n: int = 6) -> list[str]:
        """
        Break a broad topic into `n` learnable subtopics.
        Used at session start and when adapting curriculum.
        """
        prompt = f"""Decompose the following learning topic into {n} specific, learnable subtopics.
Topic: {topic}

Respond ONLY with a JSON array of short subtopic strings (max 8 words each).
Example: ["Subtopic 1", "Subtopic 2", ...]"""

        data = await self.generate_json(prompt)
        subtopics = self.coerce_string_list(data, "subtopics", "topics", "items")
        if subtopics:
            return subtopics[:n]
        return [topic]

    # ── Examine phase: test creation ───────────────────────────────────────

    async def create_test(
        self,
        topic: str,
        subtopics: list[str],
        knowledge_base: list[str],
        n_questions: int = 10,
        weak_areas: list[str] | None = None,
        evidence: str = "",
    ) -> dict[str, Any]:
        """
        Generate a test with `n_questions` multiple-choice questions.
        Returns:
          {
            "questions": [{"id": int, "question": str, "options": [str, str, str, str]}, ...],
            "answer_key": {"1": "B", "2": "A", ...}   ← index 1-based, letter A-D
          }
        The questions dict goes to the student (4B).
        The answer_key is dispatched to the grader (0.8B) separately.
        """
        kb_summary = "\n".join(f"- {s}" for s in knowledge_base[:20])
        weak_str = ", ".join(weak_areas) if weak_areas else "none identified"
        subtopic_str = ", ".join(subtopics)
        evidence_block = f"\nReliable source notes:\n{evidence}\n" if evidence else "\nReliable source notes: none provided.\n"

        prompt = f"""Create a {n_questions}-question multiple-choice test about: {topic}
Subtopics covered: {subtopic_str}
Prioritise weak areas: {weak_str}

Reference knowledge:
{kb_summary}
{evidence_block}

Each question must have exactly 4 options labelled A, B, C, D.
One option is the definitively correct answer.
Ground questions in the retrieved source notes when they are available.

Respond ONLY with a JSON object in this exact shape:
{{
  "questions": [
    {{
      "id": 1,
      "question": "Question text here?",
      "options": {{
        "A": "Option A text",
        "B": "Option B text",
        "C": "Option C text",
        "D": "Option D text"
      }}
    }}
  ],
  "answer_key": {{
    "1": "B",
    "2": "A"
  }}
}}"""

        return await self.generate_json(prompt)

    # ── Weak area analysis ─────────────────────────────────────────────────

    async def analyze_gaps(
        self,
        topic: str,
        grade_report: dict[str, Any],
        history: list[dict],
        evidence: str = "",
    ) -> list[str]:
        """
        Given a grade report from the 0.8B grader, identify which subtopics
        need the most attention next cycle.
        """
        evidence_block = f"\nReliable source notes:\n{evidence}\n" if evidence else ""
        prompt = f"""Topic: {topic}
Latest grade report: {json.dumps(grade_report)}
Score history (last {len(history)} cycles): {json.dumps(history)}
{evidence_block}

Identify up to 3 subtopics or concept areas that most need reinforcement.
Base your answer on low-scoring questions and persistent weak areas.

Respond ONLY with a JSON array of short subtopic strings.
Example: ["concept A", "concept B"]"""

        try:
            data = await self.generate_json(prompt)
            return self.coerce_string_list(data, "weak_areas", "subtopics", "gaps", "items")
        except Exception as exc:
            logger.warning("[cloud] gap analysis failed: %s", exc)
            return []
