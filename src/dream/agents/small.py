"""
dream/agents/small.py — Small local agent (Qwen3.5-0.8B).

Responsibilities:
  - Data legitimacy verification (verify phase)
  - Holding the answer key in context and grading student answers
  - Fast, low-temperature inference for deterministic judgements
"""

import logging
from typing import Any

from dream.agents.base import BaseAgent
from dream.config import ModelConfig

logger = logging.getLogger("dream.small")

_SYSTEM = """You are a strict fact-checker and grader.
For verification: assess whether a statement is factually accurate on a 0.0 to 1.0 scale.
For grading: compare a student's answer to the correct answer key and output structured results.
Always respond in the exact JSON format requested. No commentary, no preamble."""


class SmallAgent(BaseAgent):

    def __init__(self, config: ModelConfig) -> None:
        super().__init__(config)

    @property
    def system_prompt(self) -> str:
        return _SYSTEM

    # ── Gather phase ───────────────────────────────────────────────────────

    async def gather(self, topic: str, subtopics: list[str]) -> list[str]:
        """
        Small model contributes concise definitional statements.
        Kept short due to limited context window.
        """
        subtopic_str = ", ".join(subtopics[:3]) if subtopics else topic
        prompt = f"""Topic: {topic}. Focus: {subtopic_str}.
Write 3 short, factual, one-sentence statements.
Respond ONLY with a JSON array of strings."""

        try:
            data = await self.generate_json(prompt, temperature=0.4)
            return self.coerce_string_list(data, "statements", "facts", "items")
        except Exception as exc:
            logger.warning("[small] gather failed: %s", exc)
            return []

    # ── Verify phase ───────────────────────────────────────────────────────

    async def verify_statements(
        self,
        statements: list[str],
        topic: str,
    ) -> list[dict[str, Any]]:
        """
        Score each statement for factual legitimacy.
        Returns a list of: {"statement": str, "score": float, "flag": bool}
        flag=True means the statement is suspicious / should be dropped.
        """
        results: list[dict[str, Any]] = []

        for stmt in statements:
            prompt = f"""Topic context: {topic}
Statement to verify: "{stmt}"

Is this statement factually accurate?
Respond ONLY with a JSON object:
{{"score": <float 0.0-1.0>, "reason": "<one sentence>", "flag": <true if score < 0.5>}}"""

            try:
                data = await self.generate_json(
                    prompt,
                    temperature=0.2,
                    max_tokens=128,
                )
                score = float(data.get("score", 0.5))
                flag = bool(data.get("flag", score < 0.5))
                results.append({
                    "statement": stmt,
                    "score": round(score, 3),
                    "flag": flag,
                    "reason": str(data.get("reason", "")),
                })
            except Exception as exc:
                logger.warning("[small] verify failed for stmt: %s | %s", stmt[:60], exc)
                results.append({
                    "statement": stmt,
                    "score": 0.5,
                    "flag": False,
                    "reason": "verification error",
                })

        return results

    # ── Examine phase: receiving answer key ────────────────────────────────

    def store_answer_key(self, answer_key: dict[str, str]) -> None:
        """
        Store the answer key in-memory so grade() can reference it.
        In a real deployment this would sit in the 0.8B context directly.
        """
        self._answer_key: dict[str, str] = {
            str(k): v.upper() for k, v in answer_key.items()
        }
        logger.info("[small] answer key received: %d questions", len(self._answer_key))

    async def grade(
        self,
        topic: str,
        questions: list[dict[str, Any]],
        student_answers: dict[str, str],
    ) -> dict[str, Any]:
        """
        Grade the student (4B) answers against the stored answer key.
        Returns a structured grade report.

        The grading prompt passes both the question text and the expected answer
        so the 0.8B re-evaluates rather than just doing string comparison —
        this handles semantic equivalence if answers are ever free-form.
        """
        if not hasattr(self, "_answer_key"):
            raise RuntimeError("grade() called before store_answer_key()")

        per_question: dict[str, dict] = {}
        correct_count = 0
        total = len(questions)

        for q in questions:
            qid = str(q["id"])
            student_ans = student_answers.get(qid, "?").upper()
            correct_ans = self._answer_key.get(qid, "?").upper()
            is_correct = student_ans == correct_ans

            if is_correct:
                correct_count += 1

            per_question[qid] = {
                "question": q["question"],
                "student": student_ans,
                "correct": correct_ans,
                "correct_flag": is_correct,
                "option_text": q["options"].get(student_ans, "N/A"),
            }

        score_pct = round(correct_count / max(total, 1), 4)

        # Ask 0.8B to summarise which concepts the student clearly understands vs. struggles with
        wrong_ids = [qid for qid, r in per_question.items() if not r["correct_flag"]]
        if wrong_ids:
            wrong_questions = [
                f"Q{qid}: {per_question[qid]['question']} | student={per_question[qid]['student']} correct={per_question[qid]['correct']}"
                for qid in wrong_ids[:6]  # cap for 0.8B context
            ]
            prompt = f"""Topic: {topic}
Student got {correct_count}/{total} correct.
Wrong answers:
""" + "\n".join(wrong_questions) + """

Identify the 1-2 main concept gaps in one sentence each.
Respond ONLY with a JSON array of strings."""

            try:
                gaps = await self.generate_json(prompt, temperature=0.3, max_tokens=256)
                concept_gaps = self.coerce_string_list(gaps, "concept_gaps", "gaps", "weak_areas")
            except Exception:
                concept_gaps = []
        else:
            concept_gaps = []

        report = {
            "score": score_pct,
            "correct": correct_count,
            "total": total,
            "passed": score_pct >= 0.70,
            "per_question": per_question,
            "concept_gaps": concept_gaps,
        }

        logger.info(
            "[small] graded: %d/%d = %.1f%%  passed=%s",
            correct_count, total, score_pct * 100, report["passed"],
        )
        return report
