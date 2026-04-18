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
from dream.config import DreamConfig, ModelConfig
from dream.memory.dream_memory import DreamMemory
from dream.phases import (
    phase_adapt,
    phase_examine,
    phase_gather,
    phase_verify,
)
from ui.live_render import C
from ui.dream_ui import DreamLiveUI

try:
    from memory.store import MemoryStore
except Exception:
    MemoryStore = None

logger = logging.getLogger("dream.session")


def _configure_logging(log_path: str, stream: bool = True) -> None:
    fmt = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    handlers: list[logging.Handler] = [logging.FileHandler(log_path, mode="a")]
    if stream:
        handlers.insert(0, logging.StreamHandler())
    logging.basicConfig(
        level=logging.INFO,
        format=fmt,
        handlers=handlers,
        force=True,
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
        self.ui = DreamLiveUI(topic, self.cfg) if self.cfg.live_ui else None

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
        _configure_logging(self.cfg.log_path, stream=not self.cfg.live_ui)

        # Graceful shutdown on SIGTERM
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGTERM, self._request_stop)
        session_start = time.time()
        try:
            if self.ui:
                self.ui.start()
                self.ui.add_event("Dream session started.", C["brand"])
                self.ui.add_event(f"Memory file: {self.cfg.memory_path}", C["dim"])
                if self.memory_store is not None:
                    self.ui.set_backend(self.memory_store.get_status().get("backend", self.cfg.memory_backend))

            await self._prepare_cloud_lane()

            logger.info("=" * 60)
            logger.info("DREAM SESSION START")
            logger.info("Topic    : %s", self.topic)
            logger.info("Duration : %.1f hours", self.cfg.target_duration_hours)
            logger.info("Models   : cloud=%s | medium=%s | small=%s",
                        self.cfg.cloud.name, self.cfg.medium.name, self.cfg.small.name)
            logger.info("=" * 60)

            deadline = time.time() + self.cfg.target_duration_hours * 3600

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
                    self._ui_set_phase("Gather", "running", "Decomposing topic into subtopics")
                    subtopics = await cloud.decompose_topic(self.topic, n=6)
                    self.memory.subtopics = subtopics
                    logger.info("[session] subtopics: %s", subtopics)
                    self._ui_complete_phase("Gather", f"Prepared {len(subtopics)} subtopics.")
                else:
                    logger.info("[session] resuming — subtopics: %s", self.memory.subtopics)
                    self._ui_add_event("Resumed from prior Dream memory.", C["tool"])

                self._ui_set_subtopics(self.memory.subtopics)
                self._ui_update_memory()

                self._persist_session_overview()

                # Main cycle loop
                while not self._stop_flag and time.time() < deadline:
                    self._cycle += 1
                    remaining = (deadline - time.time()) / 3600
                    logger.info(
                        "\n── CYCLE %d │ %.2f hours remaining ─────────────────────────────",
                        self._cycle, remaining,
                    )
                    if self.ui:
                        self.ui.start_cycle(self._cycle, remaining, self.memory)

                    try:
                        await self._run_cycle(cloud, medium, small)
                    except KeyboardInterrupt:
                        logger.info("[session] KeyboardInterrupt — stopping cleanly")
                        self._ui_add_event("Stopped by keyboard interrupt.", C["warn"])
                        break
                    except Exception as exc:
                        logger.exception("[session] cycle %d crashed: %s", self._cycle, exc)
                        logger.info("[session] sleeping 10s before retry...")
                        self._ui_fail_phase(
                            self.ui.current_phase if self.ui else "Gather",
                            f"Cycle {self._cycle} crashed: {self._summarize_exception(exc)}",
                        )
                        await asyncio.sleep(10)
                        continue

                    # Checkpoint
                    if self._cycle % self.cfg.checkpoint_every_n_cycles == 0:
                        self.memory.save()
                        self._persist_session_overview()
                        logger.info("[session] checkpoint saved — cycle %d", self._cycle)
                        self._ui_add_event(f"Checkpoint saved at cycle {self._cycle}.", C["brand"])

                    # Convergence check
                    if self.memory.is_converged():
                        logger.info(
                            "[session] CONVERGED after %d cycles — best score=%.1f%%",
                            self._cycle, self.memory.session_best_score * 100,
                        )
                        self._ui_add_event("Convergence reached.", C["ok"])
                        break

                    # Retry limit check
                    if self.memory.topic_retry_count >= self.cfg.max_topic_retries:
                        logger.info(
                            "[session] retry limit reached (%d) — ending session",
                            self.cfg.max_topic_retries,
                        )
                        self._ui_add_event("Retry limit reached.", C["warn"])
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
            if self.ui:
                self.ui.finish(summary, elapsed)
        except Exception as exc:
            message = self._summarize_exception(exc)
            logger.error("[session] Dream failed: %s", message)
            self._ui_fail_phase(self.ui.current_phase if self.ui else "Gather", message)
            raise RuntimeError(message) from exc
        finally:
            if self.ui:
                self.ui.stop()
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
        self._ui_set_phase("Gather", "running", "Collecting candidate statements")
        raw_statements = await phase_gather(topic, memory, cloud, medium, small, cfg)
        self._ui_complete_phase("Gather", f"Collected {len(raw_statements)} candidate statements.")

        # ── Phase 2: Verify ────────────────────────────────────────────────
        await asyncio.sleep(cfg.local_inference_cooldown)
        self._ui_set_phase("Verify", "running", f"Fact-checking {len(raw_statements)} statements")
        _verified, _flagged = await phase_verify(topic, raw_statements, memory, small, cfg)
        self._persist_verified_statements(_verified)
        self._ui_complete_phase("Verify", f"Verified {len(_verified)} and flagged {len(_flagged)}.")
        self._ui_update_memory()

        # ── Phase 3: Examine ───────────────────────────────────────────────
        await asyncio.sleep(cfg.local_inference_cooldown)
        self._ui_set_phase("Examine", "running", f"Testing {len(memory.knowledge_base)} verified facts")
        grade_report = await phase_examine(topic, cycle, memory, cloud, medium, small, cfg)
        self._ui_complete_phase(
            "Examine",
            f"Scored {grade_report.get('score', 0.0) * 100:.1f}% on {grade_report.get('total', 0)} questions.",
        )

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
        self._ui_set_phase("Adapt", "running", "Updating curriculum and weak areas")
        await phase_adapt(topic, cycle, grade_report, memory, cloud, cfg)
        self._persist_cycle_report(cycle, grade_report)
        adapt_detail = (
            f"Passed cycle. Retry count {memory.topic_retry_count}/{cfg.max_topic_retries}."
            if grade_report.get("passed")
            else "Updated weak areas: " + (", ".join(memory.weak_areas[:3]) or "none")
        )
        self._ui_complete_phase("Adapt", adapt_detail)
        self._ui_update_memory(grade_report)

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
        self._ui_add_event("Received SIGTERM. Stopping after current cycle.", C["warn"])

    async def _prepare_cloud_lane(self) -> None:
        try:
            async with CloudAgent(self.cfg.cloud) as probe:
                await probe.probe()
            return
        except Exception as exc:
            primary_error = exc

        fallback_cfg = self._build_local_cloud_fallback()
        if fallback_cfg is None:
            raise RuntimeError(
                "Dream cloud orchestrator is unavailable and no local fallback is configured. "
                f"Details: {self._summarize_exception(primary_error)}"
            ) from primary_error

        try:
            async with CloudAgent(fallback_cfg) as probe:
                await probe.probe()
        except Exception as fallback_exc:
            raise RuntimeError(
                "Dream cloud orchestrator is unavailable, and the local fallback could not start. "
                f"Cloud error: {self._summarize_exception(primary_error)}. "
                f"Fallback error: {self._summarize_exception(fallback_exc)}"
            ) from fallback_exc

        logger.warning(
            "[session] cloud lane unavailable (%s); using local fallback %s via %s",
            self._summarize_exception(primary_error),
            fallback_cfg.name,
            fallback_cfg.base_url,
        )
        self.cfg.cloud = fallback_cfg
        self._ui_add_event(
            f"Cloud lane unavailable; using local fallback {fallback_cfg.name}.",
            C["warn"],
        )

    def _build_local_cloud_fallback(self) -> Optional[ModelConfig]:
        medium = self.cfg.medium
        cloud = self.cfg.cloud
        same_model = (cloud.name or "").strip() == (medium.name or "").strip()
        same_base = (cloud.base_url or "").rstrip("/") == (medium.base_url or "").rstrip("/")
        if same_model and same_base:
            return None

        return ModelConfig(
            name=medium.name,
            role="cloud-orchestrator-fallback",
            base_url=medium.base_url,
            api_key=medium.api_key,
            temperature=cloud.temperature,
            max_tokens=max(cloud.max_tokens, medium.max_tokens),
            context_window=medium.context_window,
            timeout=max(cloud.timeout, medium.timeout),
        )

    @staticmethod
    def _summarize_exception(exc: Exception) -> str:
        text = " ".join(str(exc).split()).strip()
        lowered = text.lower()
        if "incorrect api key" in lowered or "invalid_api_key" in lowered:
            return "incorrect or invalid cloud API key"
        if "401" in lowered and "api key" in lowered:
            return "cloud authentication failed (401)"
        if "connection" in lowered and "refused" in lowered:
            return "connection refused"
        if len(text) > 220:
            return text[:217] + "..."
        return text or exc.__class__.__name__

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

    def _ui_set_subtopics(self, subtopics: list[str]) -> None:
        if self.ui:
            self.ui.set_subtopics(subtopics)

    def _ui_set_phase(self, phase: str, state: str, detail: str = "") -> None:
        if self.ui:
            self.ui.set_phase(phase, state, detail)

    def _ui_complete_phase(self, phase: str, detail: str = "") -> None:
        if self.ui:
            self.ui.complete_phase(phase, detail)

    def _ui_fail_phase(self, phase: str, detail: str) -> None:
        if self.ui:
            self.ui.fail_phase(phase, detail)

    def _ui_update_memory(self, grade_report: Optional[dict] = None) -> None:
        if self.ui:
            self.ui.update_memory(self.memory, grade_report=grade_report)

    def _ui_add_event(self, text: str, color: str = C["dim"]) -> None:
        if self.ui:
            self.ui.add_event(text, color)
