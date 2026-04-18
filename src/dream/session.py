"""
dream/session.py — DreamSession: the top-level orchestration loop.

Usage:
    from dream.session import DreamSession
    from dream.config import DreamConfig

    cfg = DreamConfig(target_duration_hours=4.0)
    session = DreamSession(topic="Transformer neural architectures", config=cfg)
    asyncio.run(session.run())
"""

import asyncio
import hashlib
import json
import logging
import signal
import time
from typing import Optional

from dream.agents.cloud import CloudAgent
from dream.agents.medium import MediumAgent
from dream.agents.small import SmallAgent
from dream.config import DreamConfig
from dream.memory.dream_memory import DreamMemory
from dream.phases import (
    phase_adapt,
    phase_examine,
    phase_gather,
    phase_verify,
)

try:
    from memory.store import MemoryStore
except Exception:
    MemoryStore = None

logger = logging.getLogger("dream.session")


def _configure_logging(log_path: str) -> None:
    fmt = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=fmt,
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_path, mode="a"),
        ],
    )


class DreamSession:
    """
    Orchestrates the Dream training loop for a given topic.

    Cycle structure:
      Gather → Verify → Examine → Adapt → [checkpoint] → repeat

    Termination conditions (any of):
      - target duration reached
      - topic converged (consistently passing)
      - retry limit exceeded
      - keyboard interrupt / SIGTERM
    """

    def __init__(
        self,
        topic: str,
        config: Optional[DreamConfig] = None,
    ) -> None:
        self.topic = topic
        self.cfg = config or DreamConfig()
        self.memory = DreamMemory(self.cfg.memory_path)
        self._stop_flag = False
        self._cycle = 0
        self.memory_store = None

        if MemoryStore is not None:
            try:
                self.memory_store = MemoryStore(
                    db_url=self.cfg.memory_db_url,
                    backend=self.cfg.memory_backend,
                    require_postgres=self.cfg.require_postgres,
                )
            except Exception as exc:
                logger.warning("[session] memory store unavailable for Dream sync: %s", exc)

    # ── Entry point ────────────────────────────────────────────────────────

    async def run(self) -> None:
        _configure_logging(self.cfg.log_path)

        # Graceful shutdown on SIGTERM
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGTERM, self._request_stop)

        logger.info("=" * 60)
        logger.info("DREAM SESSION START")
        logger.info("Topic    : %s", self.topic)
        logger.info("Duration : %.1f hours", self.cfg.target_duration_hours)
        logger.info("Models   : cloud=%s | medium=%s | small=%s",
                    self.cfg.cloud.name, self.cfg.medium.name, self.cfg.small.name)
        logger.info("=" * 60)

        deadline = time.time() + self.cfg.target_duration_hours * 3600
        session_start = time.time()

        async with (
            CloudAgent(self.cfg.cloud) as cloud,
            MediumAgent(self.cfg.medium) as medium,
            SmallAgent(self.cfg.small) as small,
        ):
            # Initialise or resume memory
            resumed = self.memory.load_or_init(
                self.topic,
                [],
                resume=self.cfg.resume_existing,
            )
            if not resumed:
                logger.info("[session] decomposing topic into subtopics...")
                subtopics = await cloud.decompose_topic(self.topic, n=6)
                self.memory.subtopics = subtopics
                logger.info("[session] subtopics: %s", subtopics)
            else:
                logger.info("[session] resuming — subtopics: %s", self.memory.subtopics)

            self._persist_session_overview()

            # Main cycle loop
            while not self._stop_flag and time.time() < deadline:
                self._cycle += 1
                remaining = (deadline - time.time()) / 3600
                logger.info(
                    "\n── CYCLE %d │ %.2f hours remaining ─────────────────────────────",
                    self._cycle, remaining,
                )

                try:
                    await self._run_cycle(cloud, medium, small)
                except KeyboardInterrupt:
                    logger.info("[session] KeyboardInterrupt — stopping cleanly")
                    break
                except Exception as exc:
                    logger.exception("[session] cycle %d crashed: %s", self._cycle, exc)
                    logger.info("[session] sleeping 10s before retry...")
                    await asyncio.sleep(10)
                    continue

                # Checkpoint
                if self._cycle % self.cfg.checkpoint_every_n_cycles == 0:
                    self.memory.save()
                    self._persist_session_overview()
                    logger.info("[session] checkpoint saved — cycle %d", self._cycle)

                # Convergence check
                if self.memory.is_converged():
                    logger.info(
                        "[session] CONVERGED after %d cycles — best score=%.1f%%",
                        self._cycle, self.memory.session_best_score * 100,
                    )
                    break

                # Retry limit check
                if self.memory.topic_retry_count >= self.cfg.max_topic_retries:
                    logger.info(
                        "[session] retry limit reached (%d) — ending session",
                        self.cfg.max_topic_retries,
                    )
                    break

        # Final save and summary
        self.memory.save()
        elapsed = (time.time() - session_start) / 3600
        summary = self.memory.summary()
        logger.info("\n%s", "=" * 60)
        logger.info("DREAM SESSION COMPLETE")
        logger.info("Elapsed        : %.2f hours", elapsed)
        logger.info("Cycles         : %d", self._cycle)
        logger.info("Knowledge base : %d statements", summary["knowledge_statements"])
        logger.info("Best score     : %.1f%%", summary["best_score"] * 100)
        logger.info("Recent scores  : %s", [f"{s:.1%}" for s in summary["recent_scores"]])
        logger.info("Converged      : %s", summary["converged"])
        logger.info("=" * 60)
        self._persist_session_overview()
        if self.memory_store is not None:
            self.memory_store.close()

    # ── Single cycle ───────────────────────────────────────────────────────

    async def _run_cycle(
        self,
        cloud: CloudAgent,
        medium: MediumAgent,
        small: SmallAgent,
    ) -> None:
        cycle = self._cycle
        topic = self.topic
        memory = self.memory
        cfg = self.cfg

        # ── Phase 1: Gather ────────────────────────────────────────────────
        raw_statements = await phase_gather(topic, memory, cloud, medium, small, cfg)

        # ── Phase 2: Verify ────────────────────────────────────────────────
        await asyncio.sleep(cfg.local_inference_cooldown)
        _verified, _flagged = await phase_verify(topic, raw_statements, memory, small, cfg)
        self._persist_verified_statements(_verified)

        # ── Phase 3: Examine ───────────────────────────────────────────────
        await asyncio.sleep(cfg.local_inference_cooldown)
        grade_report = await phase_examine(topic, cycle, memory, cloud, medium, small, cfg)

        # ── Record cycle results ───────────────────────────────────────────
        memory.record_cycle(
            cycle=cycle,
            score=grade_report.get("score", 0.0),
            passed=grade_report.get("passed", False),
            concept_gaps=grade_report.get("concept_gaps", []),
            weak_areas=memory.weak_areas,
            n_statements_added=len(_verified),
        )

        # ── Phase 4: Adapt ─────────────────────────────────────────────────
        await phase_adapt(topic, cycle, grade_report, memory, cloud, cfg)
        self._persist_cycle_report(cycle, grade_report)

        # ── Cycle summary ──────────────────────────────────────────────────
        scores = memory.recent_scores(3)
        trend = "↑" if len(scores) >= 2 and scores[-1] > scores[-2] else \
                "↓" if len(scores) >= 2 and scores[-1] < scores[-2] else "→"
        logger.info(
            "[session] cycle %d done | score=%.1f%% %s | kb=%d | weak=%s",
            cycle,
            grade_report.get("score", 0) * 100,
            trend,
            len(memory.knowledge_base),
            memory.weak_areas,
        )

    def _request_stop(self) -> None:
        logger.info("[session] SIGTERM received — stopping after current cycle")
        self._stop_flag = True

    def _topic_digest(self) -> str:
        return hashlib.sha1(self.topic.encode("utf-8")).hexdigest()[:16]

    def _persist_session_overview(self) -> None:
        if self.memory_store is None:
            return

        session_id = self.cfg.session_id
        self.memory_store.get_or_create_session(
            session_id,
            model_main=self.cfg.cloud.name,
            model_local=f"{self.cfg.medium.name}, {self.cfg.small.name}",
        )
        summary = self.memory.summary()
        payload = {
            "topic": self.topic,
            "summary": summary,
            "subtopics": self.memory.subtopics,
            "memory_path": self.cfg.memory_path,
            "log_path": self.cfg.log_path,
        }
        self.memory_store.set_memory("dream:last_summary", payload, category="dream")
        self.memory_store.upsert_knowledge(
            key=f"dream:summary:{session_id}:{self._topic_digest()}",
            content=json.dumps(payload, indent=2),
            source="dream",
            category="dream_summary",
            session_id=session_id,
            metadata={"topic": self.topic, "kind": "dream_summary"},
        )

    def _persist_verified_statements(self, statements: list[str]) -> None:
        if self.memory_store is None or not statements:
            return

        session_id = self.cfg.session_id
        topic_digest = self._topic_digest()
        for statement in statements:
            statement_digest = hashlib.sha1(statement.encode("utf-8")).hexdigest()[:16]
            self.memory_store.upsert_knowledge(
                key=f"dream:knowledge:{session_id}:{topic_digest}:{statement_digest}",
                content=statement,
                source="dream",
                category="dream_knowledge",
                session_id=session_id,
                metadata={"topic": self.topic, "kind": "verified_statement"},
            )

    def _persist_cycle_report(self, cycle: int, grade_report: dict) -> None:
        if self.memory_store is None:
            return

        session_id = self.cfg.session_id
        payload = {
            "topic": self.topic,
            "cycle": cycle,
            "score": grade_report.get("score", 0.0),
            "passed": grade_report.get("passed", False),
            "concept_gaps": grade_report.get("concept_gaps", []),
            "weak_areas": self.memory.weak_areas,
            "knowledge_size": len(self.memory.knowledge_base),
        }
        self.memory_store.upsert_knowledge(
            key=f"dream:cycle:{session_id}:{self._topic_digest()}:{cycle:06d}",
            content=json.dumps(payload, indent=2),
            source="dream",
            category="dream_cycle",
            session_id=session_id,
            metadata={"topic": self.topic, "cycle": cycle, "kind": "cycle_report"},
        )
