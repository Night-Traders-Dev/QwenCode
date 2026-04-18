"""
dream/memory/dream_memory.py — Persistent knowledge store for the Dream loop.

Persists to JSON so sessions can be resumed.  The in-memory structure is:

{
  "topic": str,
  "subtopics": [str, ...],
  "knowledge_base": [str, ...],         # verified statements accumulated
  "flagged_statements": [str, ...],     # rejected by verifier
  "cycle_history": [
    {
      "cycle": int,
      "timestamp": float,
      "score": float,
      "passed": bool,
      "concept_gaps": [str],
      "weak_areas": [str],
      "n_statements_added": int,
    },
    ...
  ],
  "weak_areas": [str, ...],             # current focus areas
  "topic_retry_count": int,
  "session_best_score": float,
}
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("dream.memory")


class DreamMemory:
    """
    Thread-safe (single-event-loop) knowledge store.
    Call `save()` explicitly (session.py calls it every N cycles).
    """

    def __init__(self, path: str = "dream_memory.json") -> None:
        self._path = Path(path)
        self._data: dict[str, Any] = {}

    # ── Persistence ────────────────────────────────────────────────────────

    def load_or_init(self, topic: str, subtopics: list[str]) -> bool:
        """
        Load existing memory for `topic` if present, otherwise initialise fresh.
        Returns True if prior state was loaded (session resume).
        """
        if self._path.exists():
            try:
                with self._path.open() as f:
                    data = json.load(f)
                if data.get("topic") == topic:
                    self._data = data
                    logger.info(
                        "[memory] resumed: %d statements, %d cycles",
                        len(self._data.get("knowledge_base", [])),
                        len(self._data.get("cycle_history", [])),
                    )
                    return True
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("[memory] could not load existing file: %s", exc)

        self._data = {
            "topic": topic,
            "subtopics": subtopics,
            "knowledge_base": [],
            "flagged_statements": [],
            "cycle_history": [],
            "weak_areas": [],
            "topic_retry_count": 0,
            "session_best_score": 0.0,
        }
        logger.info("[memory] initialised fresh for topic: %s", topic)
        return False

    def save(self) -> None:
        try:
            tmp = self._path.with_suffix(".tmp")
            with tmp.open("w") as f:
                json.dump(self._data, f, indent=2)
            tmp.replace(self._path)
            logger.debug("[memory] saved to %s", self._path)
        except OSError as exc:
            logger.error("[memory] save failed: %s", exc)

    # ── Knowledge base ─────────────────────────────────────────────────────

    def add_verified_statements(self, statements: list[str]) -> int:
        """Add statements, dedup against existing. Returns count added."""
        existing = set(self._data.setdefault("knowledge_base", []))
        added = 0
        for s in statements:
            if s not in existing:
                self._data["knowledge_base"].append(s)
                existing.add(s)
                added += 1
        return added

    def add_flagged(self, statements: list[str]) -> None:
        self._data.setdefault("flagged_statements", []).extend(statements)

    @property
    def knowledge_base(self) -> list[str]:
        return self._data.get("knowledge_base", [])

    # ── Curriculum state ───────────────────────────────────────────────────

    @property
    def subtopics(self) -> list[str]:
        return self._data.get("subtopics", [])

    @subtopics.setter
    def subtopics(self, value: list[str]) -> None:
        self._data["subtopics"] = value

    @property
    def weak_areas(self) -> list[str]:
        return self._data.get("weak_areas", [])

    @weak_areas.setter
    def weak_areas(self, value: list[str]) -> None:
        self._data["weak_areas"] = value

    @property
    def topic_retry_count(self) -> int:
        return self._data.get("topic_retry_count", 0)

    def increment_retry(self) -> None:
        self._data["topic_retry_count"] = self.topic_retry_count + 1

    # ── Cycle history ──────────────────────────────────────────────────────

    def record_cycle(
        self,
        cycle: int,
        score: float,
        passed: bool,
        concept_gaps: list[str],
        weak_areas: list[str],
        n_statements_added: int,
    ) -> None:
        entry = {
            "cycle": cycle,
            "timestamp": time.time(),
            "score": round(score, 4),
            "passed": passed,
            "concept_gaps": concept_gaps,
            "weak_areas": weak_areas,
            "n_statements_added": n_statements_added,
        }
        self._data.setdefault("cycle_history", []).append(entry)

        if score > self._data.get("session_best_score", 0.0):
            self._data["session_best_score"] = score

    @property
    def cycle_history(self) -> list[dict]:
        return self._data.get("cycle_history", [])

    @property
    def session_best_score(self) -> float:
        return self._data.get("session_best_score", 0.0)

    def recent_scores(self, n: int = 5) -> list[float]:
        return [e["score"] for e in self.cycle_history[-n:]]

    def is_converged(self, window: int = 3, threshold: float = 0.02) -> bool:
        """
        Returns True if the last `window` scores are all above passing
        and their range is < `threshold` (i.e. the model has plateaued).
        """
        scores = self.recent_scores(window)
        if len(scores) < window:
            return False
        all_passing = all(s >= 0.70 for s in scores)
        score_range = max(scores) - min(scores)
        return all_passing and score_range < threshold

    # ── Diagnostics ────────────────────────────────────────────────────────

    def summary(self) -> dict[str, Any]:
        return {
            "topic": self._data.get("topic"),
            "total_cycles": len(self.cycle_history),
            "knowledge_statements": len(self.knowledge_base),
            "flagged_statements": len(self._data.get("flagged_statements", [])),
            "best_score": self.session_best_score,
            "recent_scores": self.recent_scores(5),
            "weak_areas": self.weak_areas,
            "converged": self.is_converged(),
        }
