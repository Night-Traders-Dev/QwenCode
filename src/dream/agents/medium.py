"""
dream/agents/medium.py — Medium local agent (Qwen3.5-4B).

Responsibilities:
  - Focused data gathering from a student perspective
  - Taking tests produced by the cloud agent
  - Asking clarifying questions (future: self-directed learning)
"""

import json
import logging
from typing import Any

from dream.agents.base import BaseAgent
from dream.config import ModelConfig

logger = logging.getLogger("dream.medium")

_SYSTEM = """You are a diligent student model learning about topics by reading provided information
and answering questions to the best of your ability.
When answering multiple-choice questions, only output the letter of your chosen answer (A, B, C, or D).
When gathering knowledge, provide concise factual statements."""


class MediumAgent(BaseAgent):

    def __init__(self, config: ModelConfig) -> None:
        super().__init__(config)

    @property
    def system_prompt(self) -> str:
        return _SYSTEM

    # ── Gather phase ───────────────────────────────────────────────────────

    async def gather(self, topic: str, subtopics: list[str], evidence: str = "") -> list[str]:
        """
        Produce focused factual statements from a learner's perspective.
        Tends to surface concrete examples and common misconceptions.
        """
        subtopic_str = ", ".join(subtopics) if subtopics else topic
        evidence_block = f"\nReliable source notes:\n{evidence}\n" if evidence else "\nReliable source notes: none provided.\n"
        prompt = f"""You are learning about: {topic}
Focus on: {subtopic_str}
{evidence_block}

Write 5 factual, specific statements about this topic.
Include at least one concrete example and one common misconception correction.
Prefer facts supported by the reliable source notes when they are available.

Respond ONLY with a JSON array of strings.
Example: ["Fact one.", "Fact two.", ...]"""

        try:
            data = await self.generate_json(prompt)
            return self.coerce_string_list(data, "statements", "facts", "items")
        except Exception as exc:
            logger.warning("[medium] gather failed: %s", exc)
            return []

    # ── Examine phase: test taking ─────────────────────────────────────────

    async def take_test(
        self,
        topic: str,
        questions: list[dict[str, Any]],
        knowledge_base: list[str],
    ) -> dict[str, str]:
        """
        Answer a list of multiple-choice questions.
        `knowledge_base` provides context gathered this cycle (no answers).

        Returns: {"1": "B", "2": "C", ...}  — student answers keyed by question id.
        """
        if not questions:
            return {}

        kb_text = "\n".join(f"- {s}" for s in knowledge_base[:15])
        prompt_questions = []
        for q in questions:
            qid = str(q["id"])
            options_text = "\n".join(
                f"  {letter}) {text}"
                for letter, text in q["options"].items()
            )
            prompt_questions.append(f"Question {qid}: {q['question']}\n{options_text}")

        prompt = f"""You have been studying: {topic}

Reference material:
{kb_text}

Answer all questions. Return ONLY a JSON object mapping question id to one letter answer.
Example: {{"1": "B", "2": "C"}}

Questions:
{chr(10).join(prompt_questions)}"""

        try:
            data = await self.generate_json(
                prompt,
                temperature=0.1,
                max_tokens=max(128, len(questions) * 24),
                response_format={"type": "json_object"},
            )
            answers: dict[str, str] = {}
            if isinstance(data, dict):
                for q in questions:
                    qid = str(q["id"])
                    raw = str(data.get(qid, "A")).upper()
                    answers[qid] = next((c for c in raw if c in "ABCD"), "A")
            if len(answers) == len(questions):
                return answers
            raise ValueError("missing answers in batch response")
        except Exception as exc:
            logger.warning("[medium] batch test failed, falling back to per-question mode: %s", exc)

        answers: dict[str, str] = {}
        for q in questions:
            qid = str(q["id"])
            options_text = "\n".join(
                f"  {letter}) {text}"
                for letter, text in q["options"].items()
            )
            single_prompt = f"""You have been studying: {topic}

Reference material:
{kb_text}

Question {qid}: {q['question']}
{options_text}

Answer with ONLY the letter of the correct option (A, B, C, or D). No explanation."""

            try:
                response = await self.generate(
                    single_prompt,
                    temperature=0.1,
                    max_tokens=8,
                )
                answers[qid] = next((c for c in response.upper() if c in "ABCD"), "A")
            except Exception as exc:
                logger.warning("[medium] question %s failed: %s", qid, exc)
                answers[qid] = "A"

        return answers

    # ── Self-assessment ────────────────────────────────────────────────────

    async def reflect(
        self,
        topic: str,
        grade_report: dict[str, Any],
    ) -> list[str]:
        """
        After grading, the student identifies what it got wrong and why.
        Returns a list of confusion/gap statements stored in memory.
        """
        wrong = [
            f"Q{qid}: answered {r['student']}, correct was {r['correct']}"
            for qid, r in grade_report.get("per_question", {}).items()
            if not r.get("correct_flag", False)
        ]
        if not wrong:
            return []

        wrong_str = "\n".join(wrong)
        prompt = f"""Topic: {topic}
You got these questions wrong:
{wrong_str}

For each mistake, write one sentence describing the knowledge gap it reveals.
Respond ONLY with a JSON array of strings."""

        try:
            data = await self.generate_json(prompt)
            return self.coerce_string_list(data, "reflection", "gaps", "items", "statements")
        except Exception as exc:
            logger.warning("[medium] reflection failed: %s", exc)
            return []
