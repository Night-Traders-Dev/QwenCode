"""
dream/phases/__init__.py — The four Dream phases as composable async functions.

Each phase is a pure async function that takes agents + memory and returns results.
The session.py orchestrator calls them in sequence each cycle.
"""

import asyncio
import logging
import time
from typing import Any

from dream.agents.cloud import CloudAgent
from dream.agents.medium import MediumAgent
from dream.agents.small import SmallAgent
from dream.config import DreamConfig
from dream.memory.dream_memory import DreamMemory

logger = logging.getLogger("dream.phases")


def _is_local_endpoint(base_url: str) -> bool:
    lowered = (base_url or "").lower()
    return "localhost:11434" in lowered or "127.0.0.1:11434" in lowered


# ── Phase 1: Gather ────────────────────────────────────────────────────────

async def phase_gather(
    topic: str,
    memory: DreamMemory,
    cloud: CloudAgent,
    medium: MediumAgent,
    small: SmallAgent,
    cfg: DreamConfig,
) -> list[str]:
    """
    All three agents generate factual statements about the topic.
    Returns the combined raw (unverified) list.

    Cloud and medium run concurrently; small waits (VRAM budget on 8GB).
    """
    logger.info("[gather] starting — topic=%s, subtopics=%s", topic, memory.subtopics[:3])
    t0 = time.perf_counter()

    # Only parallelize when the cloud lane is truly remote. If it has fallen back
    # to a local endpoint, keep the local 4B calls serialized to avoid VRAM spikes.
    if _is_local_endpoint(cfg.cloud.base_url):
        cloud_stmts = await cloud.gather(topic, memory.subtopics)
        await asyncio.sleep(cfg.local_inference_cooldown)
        medium_stmts = await medium.gather(topic, memory.subtopics)
    else:
        cloud_task = asyncio.create_task(cloud.gather(topic, memory.subtopics))
        medium_task = asyncio.create_task(medium.gather(topic, memory.subtopics))
        cloud_stmts, medium_stmts = await asyncio.gather(cloud_task, medium_task)

    # Small runs after medium to avoid OOM spike on shared 8GB
    await asyncio.sleep(cfg.local_inference_cooldown)
    small_stmts = await small.gather(topic, memory.subtopics)

    combined = cloud_stmts + medium_stmts + small_stmts

    logger.info(
        "[gather] complete in %.1fs | cloud=%d medium=%d small=%d total=%d",
        time.perf_counter() - t0,
        len(cloud_stmts), len(medium_stmts), len(small_stmts), len(combined),
    )
    return combined


# ── Phase 2: Verify ────────────────────────────────────────────────────────

async def phase_verify(
    topic: str,
    raw_statements: list[str],
    memory: DreamMemory,
    small: SmallAgent,
    cfg: DreamConfig,
) -> tuple[list[str], list[str]]:
    """
    0.8B model scores each statement.
    Returns (verified_statements, flagged_statements).
    Verified statements are added to DreamMemory.
    """
    logger.info("[verify] checking %d statements", len(raw_statements))
    t0 = time.perf_counter()

    # Deduplicate raw before sending to verifier
    seen: set[str] = set()
    unique: list[str] = []
    for s in raw_statements:
        norm = s.strip().lower()
        if norm not in seen:
            seen.add(norm)
            unique.append(s.strip())

    results = await small.verify_statements(unique, topic)

    verified: list[str] = []
    flagged: list[str] = []

    for r in results:
        if not r["flag"] and r["score"] >= cfg.min_verify_confidence:
            verified.append(r["statement"])
        else:
            flagged.append(r["statement"])
            logger.debug(
                "[verify] FLAGGED (%.2f): %s", r["score"], r["statement"][:80]
            )

    n_added = memory.add_verified_statements(verified)
    memory.add_flagged(flagged)

    logger.info(
        "[verify] done in %.1fs | verified=%d flagged=%d new_to_kb=%d",
        time.perf_counter() - t0, len(verified), len(flagged), n_added,
    )
    return verified, flagged


# ── Phase 3: Examine (Test + Grade) ───────────────────────────────────────

async def phase_examine(
    topic: str,
    cycle: int,
    memory: DreamMemory,
    cloud: CloudAgent,
    medium: MediumAgent,
    small: SmallAgent,
    cfg: DreamConfig,
) -> dict[str, Any]:
    """
    Full test cycle:
      1. Cloud creates test + answer key
      2. Answer key sent to small (grader); student gets questions only
      3. Medium takes the test
      4. Small grades; medium reflects on mistakes

    Returns the grade report dict.
    """
    t0 = time.perf_counter()
    kb = memory.knowledge_base
    weak = memory.weak_areas

    logger.info(
        "[examine] cycle=%d | kb_size=%d | weak_areas=%s",
        cycle, len(kb), weak,
    )

    # 3a — Cloud creates test
    logger.info("[examine] cloud creating test...")
    test_data = await cloud.create_test(
        topic=topic,
        subtopics=memory.subtopics,
        knowledge_base=kb,
        n_questions=cfg.questions_per_test,
        weak_areas=weak,
    )

    questions: list[dict] = test_data.get("questions", [])
    answer_key: dict[str, str] = test_data.get("answer_key", {})

    if not questions:
        logger.error("[examine] cloud returned no questions — skipping cycle")
        return {"score": 0.0, "correct": 0, "total": 0, "passed": False,
                "per_question": {}, "concept_gaps": [], "error": "no questions"}

    # 3b — Dispatch answer key to small (grader)
    small.store_answer_key(answer_key)
    logger.info("[examine] answer key stored in small agent (%d answers)", len(answer_key))

    # 3c — Medium takes the test (no answer key in context)
    await asyncio.sleep(cfg.local_inference_cooldown)
    logger.info("[examine] medium taking test (%d questions)...", len(questions))
    student_answers = await medium.take_test(
        topic=topic,
        questions=questions,
        knowledge_base=kb,
    )

    # 3d — Small grades
    await asyncio.sleep(cfg.local_inference_cooldown)
    logger.info("[examine] small grading...")
    grade_report = await small.grade(
        topic=topic,
        questions=questions,
        student_answers=student_answers,
    )

    # 3e — Medium reflects on mistakes (async, fire-and-capture)
    await asyncio.sleep(cfg.local_inference_cooldown)
    reflection = await medium.reflect(topic, grade_report)
    if reflection:
        memory.add_verified_statements(reflection)
        logger.info("[examine] medium reflection added %d gap notes", len(reflection))

    elapsed = time.perf_counter() - t0
    logger.info(
        "[examine] done in %.1fs | score=%.1f%% passed=%s",
        elapsed, grade_report["score"] * 100, grade_report["passed"],
    )
    return grade_report


# ── Phase 4: Adapt ─────────────────────────────────────────────────────────

async def phase_adapt(
    topic: str,
    cycle: int,
    grade_report: dict[str, Any],
    memory: DreamMemory,
    cloud: CloudAgent,
    cfg: DreamConfig,
) -> None:
    """
    Cloud analyses the grade report and updates the curriculum (weak_areas).
    Updates DreamMemory in-place, no return value.
    """
    history = memory.cycle_history[-5:]  # last 5 for context

    if grade_report.get("passed"):
        memory.increment_retry()
        logger.info(
            "[adapt] passed — retry_count=%d/%d",
            memory.topic_retry_count, cfg.max_topic_retries,
        )
    else:
        weak_areas = await cloud.analyze_gaps(topic, grade_report, history)
        memory.weak_areas = weak_areas
        logger.info("[adapt] new weak_areas: %s", weak_areas)
