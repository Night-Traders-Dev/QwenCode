"""
dream/memory/dream_memory.py — Persistent knowledge store for the Dream loop.
"""

import json
import logging
import math
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any

logger = logging.getLogger("dream.memory")


def _summarize_and_trim(history: list[dict[str, Any]], keep: int = 80) -> None:
    if len(history) <= keep:
        return

    trim_count = len(history) - keep + 1
    chunk = history[:trim_count]
    scores = [
        float(item.get("score", 0.0))
        for item in chunk
        if isinstance(item, dict)
    ]
    summary = {
        "summary": True,
        "timestamp": time.time(),
        "count": len(chunk),
        "avg_score": round(sum(scores) / len(scores), 4) if scores else 0.0,
        "min_score": round(min(scores), 4) if scores else 0.0,
        "max_score": round(max(scores), 4) if scores else 0.0,
    }
    del history[:trim_count]
    history.insert(0, summary)


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

        kb = self._data.get("knowledge_base", [])
        if kb and isinstance(kb[0], str):
            self._data["knowledge_base"] = [
                {"statement": s, "cycle": 0} for s in kb
            ]

        for entry in self._data.get("cycle_history", []):
            if isinstance(entry, dict):
                entry.setdefault("concept_mastery_snapshot", {})

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

    def add_verified_statements(self, statements: list[str], cycle: int = 0) -> int:
        existing = {
            item["statement"]
            for item in self._data.setdefault("knowledge_base", [])
            if isinstance(item, dict) and "statement" in item
        }
        added = 0
        for s in statements:
            if s not in existing:
                self._data["knowledge_base"].append({"statement": s, "cycle": cycle})
                existing.add(s)
                added += 1
        return added

    def add_flagged(self, statements: list[str]) -> None:
        self._data.setdefault("flagged_statements", []).extend(statements)

    @property
    def knowledge_base(self) -> list[str]:
        return [
            item["statement"]
            for item in self._data.get("knowledge_base", [])
            if isinstance(item, dict) and "statement" in item
        ]

    @property
    def _knowledge_entries(self) -> list[dict[str, Any]]:
        return [
            item for item in self._data.get("knowledge_base", [])
            if isinstance(item, dict) and "statement" in item
        ]

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
        if len(history) > 100:
            _summarize_and_trim(history, keep=80)

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
            if not area:
                continue
            prior = float(concept_mastery.get(area, 0.0) or 0.0)
            delta = 0.18 if passed else (-0.12 if score_delta < 0 else -0.04)
            concept_mastery[area] = round(max(-2.0, min(2.0, prior + delta)), 4)

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
        if len(history) > 100:
            _summarize_and_trim(history, keep=80)

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
            "concept_mastery_snapshot": dict(self.concept_mastery),
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

    def is_converged(self, window: int = 3, threshold: float = 0.02, min_kb_size: int = 20) -> bool:
        scores = self.recent_scores(window)
        if len(scores) < window:
            return False
        all_passing = all(s >= 0.70 for s in scores)
        score_range = max(scores) - min(scores)
        kb_mature = len(self.knowledge_base) >= min_kb_size
        no_weak_areas = len(self.weak_areas) == 0
        return all_passing and score_range < threshold and kb_mature and no_weak_areas

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

    # ── Dream Replay & Distillation ────────────────────────────────────────

    def get_high_confidence_statements(self, min_cycle_score: float = 0.85, limit: int = 50) -> list[str]:
        high_score_cycles = {
            cycle["cycle"]
            for cycle in self.cycle_history
            if cycle.get("score", 0.0) >= min_cycle_score
        }

        if not high_score_cycles:
            return self.knowledge_base[-limit:] if self.knowledge_base else []

        matched = [
            item["statement"]
            for item in self._knowledge_entries
            if int(item.get("cycle", 0)) in high_score_cycles
        ]
        return matched[-limit:]

    def generate_distillation_dataset(self, output_path: str = "distillation_data.json") -> int:
        samples = []
        high_conf_statements = self.get_high_confidence_statements()

        for statement in high_conf_statements:
            concepts = self._extract_key_concepts(statement)
            sample = {
                "instruction": "Explain the following verified fact clearly and completely:",
                "input": statement,
                "output": f"{statement}\n\nKey concepts: {concepts}",
                "metadata": {
                    "source": "dream_memory",
                    "topic": self._data.get("topic", ""),
                    "confidence": "high",
                },
            }
            samples.append(sample)

        try:
            with open(output_path, "w") as f:
                json.dump(samples, f, indent=2)
            logger.info("[memory] generated %d distillation samples to %s", len(samples), output_path)
            return len(samples)
        except OSError as exc:
            logger.error("[memory] failed to write distillation dataset: %s", exc)
            return 0

    def _extract_key_concepts(self, statement: str) -> str:
        stopwords = {
            "the", "and", "that", "this", "with", "from", "have", "been",
            "are", "was", "for", "not", "but", "they", "will", "its",
            "has", "can", "all", "an", "a", "in", "of", "to", "is",
            "it", "be", "as", "at", "by", "we", "or", "on", "so",
        }

        def tokenize(text: str) -> list[str]:
            return [
                w.lower()
                for w in re.findall(r"[a-zA-Z]+", text)
                if len(w) > 3 and w.lower() not in stopwords
            ]

        target_terms = tokenize(statement)
        if not target_terms:
            return ""

        kb = self.knowledge_base
        if len(kb) < 5:
            counts = Counter(target_terms)
            return ", ".join(t for t, _ in counts.most_common(5))

        doc_freq: Counter[str] = Counter()
        for doc in kb:
            for term in set(tokenize(doc)):
                doc_freq[term] += 1

        n_docs = len(kb)
        tfidf: dict[str, float] = {}
        term_counts = Counter(target_terms)
        for term, tf in term_counts.items():
            df = doc_freq.get(term, 0)
            idf = math.log((n_docs + 1) / (df + 1)) + 1.0
            tfidf[term] = tf * idf

        top = sorted(tfidf.items(), key=lambda x: x[1], reverse=True)[:5]
        return ", ".join(t for t, _ in top)

    def cross_topic_search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        stopwords = {"the", "and", "that", "this", "with", "from", "have", "been", "are", "a", "an", "in", "of"}

        def tokenize(text: str) -> list[str]:
            return [w.lower() for w in re.findall(r"[a-zA-Z]+", text) if w.lower() not in stopwords]

        kb = self.knowledge_base
        if not kb:
            return []

        query_terms = tokenize(query)
        if not query_terms:
            return []

        doc_freq: Counter[str] = Counter()
        tokenized_docs = [tokenize(stmt) for stmt in kb]
        for tokens in tokenized_docs:
            for term in set(tokens):
                doc_freq[term] += 1

        n_docs = len(kb)
        avg_dl = sum(len(t) for t in tokenized_docs) / max(1, n_docs)
        k1 = 1.5
        b = 0.75

        results = []
        for stmt, tokens in zip(kb, tokenized_docs):
            tf_map = Counter(tokens)
            dl = len(tokens)
            score = 0.0
            for term in query_terms:
                if term not in tf_map:
                    continue
                df = doc_freq.get(term, 0)
                idf = math.log((n_docs - df + 0.5) / (df + 0.5) + 1.0)
                tf = tf_map[term]
                numerator = tf * (k1 + 1)
                denominator = tf + k1 * (1 - b + b * dl / max(avg_dl, 1.0))
                score += idf * numerator / denominator

            if score > 0:
                results.append({
                    "statement": stmt,
                    "relevance_score": round(score, 4),
                    "topic": self._data.get("topic", ""),
                })

        results.sort(key=lambda x: x["relevance_score"], reverse=True)
        return results[:limit]

    def create_concept_map(self) -> dict[str, list[str]]:
        concept_groups: dict[str, list[str]] = {}

        for statement in self.knowledge_base:
            concepts = self._extract_key_concepts(statement).split(", ")
            for concept in concepts:
                if concept:
                    concept_groups.setdefault(concept, []).append(statement)

        return {k: v for k, v in concept_groups.items() if len(v) >= 2}