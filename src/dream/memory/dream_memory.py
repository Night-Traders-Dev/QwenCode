"""
dream/memory/dream_memory.py — Persistent knowledge store for the Dream loop.

Persists to JSON so sessions can be resumed.  The in-memory structure is:

{
  "topic": str,
  "subtopics": [str, ...],
  "knowledge_base": [str, ...],         # verified statements accumulated
  "current_research": {
    "query": str,
    "focus_terms": [str, ...],
    "sources": [{"title": str, "url": str, "domain": str, "snippet": str}, ...],
    "candidate_statements": [str, ...],
    "timestamp": float,
  },
  "research_history": [current_research, ...],
  "reinforcement": {
    "concept_mastery": {str: float, ...},
    "source_rewards": {str: float, ...},
    "history": [{"reward": float, "score_delta": float, ...}, ...],
  },
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

    def load_or_init(self, topic: str, subtopics: list[str], resume: bool = False) -> bool:
        """
        Load existing memory for `topic` if present, otherwise initialise fresh.
        Returns True if prior state was loaded (session resume).
        """
        if resume and self._path.exists():
            try:
                with self._path.open() as f:
                    data = json.load(f)
                if data.get("topic") == topic:
                    self._data = data
                    self._normalize_shape()
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
            "current_research": {
                "query": "",
                "focus_terms": [],
                "sources": [],
                "candidate_statements": [],
                "timestamp": 0.0,
            },
            "research_history": [],
            "reinforcement": {
                "concept_mastery": {},
                "source_rewards": {},
                "history": [],
            },
            "flagged_statements": [],
            "cycle_history": [],
            "weak_areas": [],
            "topic_retry_count": 0,
            "session_best_score": 0.0,
        }
        if self._path.exists() and not resume:
            logger.info("[memory] starting fresh; existing file will be replaced on save: %s", self._path)
        else:
            logger.info("[memory] initialised fresh for topic: %s", topic)
        return False

    def _normalize_shape(self) -> None:
        self._data.setdefault("knowledge_base", [])
        self._data.setdefault("flagged_statements", [])
        self._data.setdefault("cycle_history", [])
        self._data.setdefault("weak_areas", [])
        self._data.setdefault("topic_retry_count", 0)
        self._data.setdefault("session_best_score", 0.0)
        self._data.setdefault(
            "current_research",
            {
                "query": "",
                "focus_terms": [],
                "sources": [],
                "candidate_statements": [],
                "timestamp": 0.0,
            },
        )
        self._data.setdefault("research_history", [])
        self._data.setdefault(
            "reinforcement",
            {
                "concept_mastery": {},
                "source_rewards": {},
                "history": [],
            },
        )
        reinforcement = self._data.get("reinforcement", {})
        if isinstance(reinforcement, dict):
            reinforcement.setdefault("concept_mastery", {})
            reinforcement.setdefault("source_rewards", {})
            reinforcement.setdefault("history", [])

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

    # ── Internet research ─────────────────────────────────────────────────

    def set_research(
        self,
        query: str,
        focus_terms: list[str],
        sources: list[dict[str, Any]],
        candidate_statements: list[str],
    ) -> None:
        payload = {
            "query": query,
            "focus_terms": [str(item).strip() for item in focus_terms if str(item).strip()],
            "sources": [source for source in sources if isinstance(source, dict) and source.get("url")],
            "candidate_statements": [str(item).strip() for item in candidate_statements if str(item).strip()],
            "timestamp": time.time(),
        }
        self._data["current_research"] = payload
        history = self._data.setdefault("research_history", [])
        history.append(payload)
        if len(history) > 12:
            del history[:-12]

    @property
    def current_research(self) -> dict[str, Any]:
        return self._data.get("current_research", {})

    @property
    def research_query(self) -> str:
        return str(self.current_research.get("query", ""))

    @property
    def research_sources(self) -> list[dict[str, Any]]:
        return [
            source
            for source in self.current_research.get("sources", [])
            if isinstance(source, dict) and source.get("url")
        ]

    @property
    def research_candidate_statements(self) -> list[str]:
        return [
            str(item).strip()
            for item in self.current_research.get("candidate_statements", [])
            if str(item).strip()
        ]

    @property
    def research_domains(self) -> list[str]:
        seen: set[str] = set()
        domains: list[str] = []
        for source in self.research_sources:
            domain = str(source.get("domain", "")).strip()
            if domain and domain not in seen:
                seen.add(domain)
                domains.append(domain)
        return domains

    @property
    def research_timestamp(self) -> float:
        try:
            return float(self.current_research.get("timestamp", 0.0) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def evidence_block(self, max_chars: int = 2400) -> str:
        blocks: list[str] = []
        total = 0
        for idx, source in enumerate(self.research_sources, start=1):
            block = (
                f"[{idx}] {source.get('title', 'Untitled source')} ({source.get('domain', 'unknown')})\n"
                f"URL: {source.get('url', '')}\n"
                f"Summary: {source.get('snippet', '')}"
            )
            if total and total + len(block) + 2 > max_chars:
                break
            blocks.append(block)
            total += len(block) + 2
        return "\n\n".join(blocks)

    # ── Reinforcement state ───────────────────────────────────────────────

    @property
    def reinforcement(self) -> dict[str, Any]:
        return self._data.get("reinforcement", {})

    @property
    def concept_mastery(self) -> dict[str, float]:
        mastery = self.reinforcement.get("concept_mastery", {})
        return {
            str(key): float(value)
            for key, value in mastery.items()
            if str(key).strip()
        }

    @property
    def source_rewards(self) -> dict[str, float]:
        rewards = self.reinforcement.get("source_rewards", {})
        return {
            str(key): float(value)
            for key, value in rewards.items()
            if str(key).strip()
        }

    def reinforcement_focus(self, limit: int = 3) -> list[str]:
        ordered = sorted(self.concept_mastery.items(), key=lambda item: item[1])
        focus: list[str] = []
        for name, _score in ordered:
            if name not in focus:
                focus.append(name)
            if len(focus) >= limit:
                break
        for item in self.weak_areas:
            cleaned = str(item).strip()
            if cleaned and cleaned not in focus:
                focus.append(cleaned)
            if len(focus) >= limit:
                break
        return focus[:limit]

    def reinforce_cycle(
        self,
        score: float,
        passed: bool,
        concept_gaps: list[str],
        weak_areas: list[str],
        n_statements_added: int,
        sources: list[dict[str, Any]] | None = None,
        focus_terms: list[str] | None = None,
    ) -> None:
        reinforcement = self._data.setdefault(
            "reinforcement",
            {
                "concept_mastery": {},
                "source_rewards": {},
                "history": [],
            },
        )
        concept_mastery = reinforcement.setdefault("concept_mastery", {})
        source_rewards = reinforcement.setdefault("source_rewards", {})
        history = reinforcement.setdefault("history", [])

        previous_score = 0.0
        if len(self.cycle_history) >= 2:
            previous_score = float(self.cycle_history[-2].get("score", 0.0) or 0.0)
        score_delta = score - previous_score
        base_reward = max(
            -1.0,
            min(
                1.0,
                (score - 0.5) * 1.2
                + score_delta * 0.8
                + min(n_statements_added, 6) * 0.03
                + (0.15 if passed else -0.05),
            ),
        )

        active_terms = [str(item).strip() for item in weak_areas if str(item).strip()]
        if not active_terms:
            active_terms = [str(item).strip() for item in (focus_terms or []) if str(item).strip()]

        for area in active_terms:
            cleaned = str(area).strip()
            if not cleaned:
                continue
            prior = float(concept_mastery.get(cleaned, 0.0) or 0.0)
            delta = 0.18 if passed else (-0.12 if score_delta < 0 else -0.04)
            concept_mastery[cleaned] = round(max(-2.0, min(2.0, prior + delta)), 4)

        for gap in concept_gaps:
            cleaned = str(gap).strip()
            if not cleaned:
                continue
            prior = float(concept_mastery.get(cleaned, 0.0) or 0.0)
            concept_mastery[cleaned] = round(max(-2.0, min(2.0, prior - 0.3)), 4)

        for source in sources or []:
            if not isinstance(source, dict):
                continue
            domain = str(source.get("domain", "")).strip()
            if not domain:
                continue
            prior = float(source_rewards.get(domain, 0.0) or 0.0)
            source_rewards[domain] = round(max(-2.0, min(2.0, prior + base_reward * 0.25)), 4)

        history.append(
            {
                "timestamp": time.time(),
                "reward": round(base_reward, 4),
                "score": round(score, 4),
                "score_delta": round(score_delta, 4),
                "passed": bool(passed),
                "weak_areas": [str(item).strip() for item in weak_areas if str(item).strip()],
                "concept_gaps": [str(item).strip() for item in concept_gaps if str(item).strip()],
            }
        )
        if len(history) > 24:
            del history[:-24]

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
            "research_sources": len(self.research_sources),
            "research_domains": self.research_domains[:4],
            "research_query": self.research_query,
            "reinforcement_focus": self.reinforcement_focus(3),
            "best_score": self.session_best_score,
            "recent_scores": self.recent_scores(5),
            "weak_areas": self.weak_areas,
            "converged": self.is_converged(),
        }
