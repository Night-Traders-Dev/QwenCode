"""
dream/phases/__init__.py — The four Dream phases as composable async functions.
"""

import asyncio
import json
import logging
import time
from typing import Any

from dream.agents.cloud import CloudAgent
from dream.agents.medium import MediumAgent
from dream.agents.small import SmallAgent
from dream.config import DreamConfig
from dream.memory.dream_memory import DreamMemory
from dream.research import DreamResearcher, ResearchPacket, ResearchSource

logger = logging.getLogger("dream.phases")


def _is_local_endpoint(base_url: str) -> bool:
    lowered = (base_url or "").lower()
    return "localhost:11434" in lowered or "127.0.0.1:11434" in lowered


async def _research_packet(
    topic: str,
    memory: DreamMemory,
    cfg: DreamConfig,
) -> ResearchPacket | None:
    if not cfg.research_enabled:
        return None

    researcher = DreamResearcher(cfg)
    query, _focus = researcher.build_query(
        topic,
        memory.subtopics,
        memory.weak_areas,
        memory.reinforcement_focus(3),
    )
    research_age = max(0.0, time.time() - memory.research_timestamp)
    if (
        memory.research_query == query
        and memory.research_sources
        and research_age <= cfg.research_refresh_seconds
    ):
        logger.info(
            "[research] reusing cached sources for query=%r | sources=%d | age=%.1fs",
            query,
            len(memory.research_sources),
            research_age,
        )
        return ResearchPacket.from_memory_payload(memory.current_research)

    try:
        packet = await researcher.collect(
            topic,
            memory.subtopics,
            memory.weak_areas,
            memory.reinforcement_focus(3),
        )
    except Exception as exc:
        logger.warning("[research] collection failed for %r: %s", query, exc)
        return None

    memory.set_research(
        query=packet.query,
        focus_terms=packet.focus_terms,
        sources=[source.as_dict() for source in packet.sources],
        candidate_statements=packet.candidate_statements,
    )
    return packet


def _stored_research_packet(
    topic: str,
    memory: DreamMemory,
    cfg: DreamConfig,
    memory_store: Any = None,
) -> ResearchPacket | None:
    if memory_store is None:
        return None

    try:
        rows = memory_store.list_knowledge(
            limit=max(cfg.research_max_sources * 2, 6),
            category="dream_source",
            metadata={"topic": topic},
        )
    except Exception as exc:
        logger.warning("[research] stored-source lookup failed for %r: %s", topic, exc)
        return None

    sources: list[ResearchSource] = []
    seen_urls: set[str] = set()
    for row in rows:
        try:
            payload = json.loads(row.get("content", "") or "{}")
        except json.JSONDecodeError:
            continue
        url = str(payload.get("url", "")).strip()
        snippet = str(payload.get("snippet", "")).strip()
        if not url or not snippet or url in seen_urls:
            continue
        seen_urls.add(url)
        sources.append(
            ResearchSource(
                title=str(payload.get("title", "") or row.get("source") or "Stored source"),
                url=url,
                domain=str(payload.get("domain", "")).strip(),
                snippet=snippet[: cfg.research_chars_per_source],
                query=str(payload.get("query", topic)).strip(),
            )
        )
        if len(sources) >= cfg.research_max_sources:
            break

    if not sources:
        return None

    researcher = DreamResearcher(cfg)
    return ResearchPacket(
        query=memory.research_query or topic,
        focus_terms=memory.reinforcement_focus(3),
        sources=sources,
        candidate_statements=researcher._distill_candidate_statements(sources),
    )


def _merge_research_packets(
    primary: ResearchPacket | None,
    supplement: ResearchPacket | None,
    cfg: DreamConfig,
) -> ResearchPacket | None:
    if primary is None:
        return supplement
    if supplement is None:
        return primary

    sources: list[ResearchSource] = []
    seen_urls: set[str] = set()
    for source in primary.sources + supplement.sources:
        if source.url in seen_urls:
            continue
        seen_urls.add(source.url)
        sources.append(source)
        if len(sources) >= cfg.research_max_sources:
            break

    seen_statements: set[str] = set()
    statements: list[str] = []
    for statement in primary.candidate_statements + supplement.candidate_statements:
        normalized = statement.strip().lower()
        if not normalized or normalized in seen_statements:
            continue
        seen_statements.add(normalized)
        statements.append(statement.strip())
        if len(statements) >= cfg.research_statement_limit:
            break

    focus_terms = []
    for item in primary.focus_terms + supplement.focus_terms:
        cleaned = item.strip()
        if cleaned and cleaned not in focus_terms:
            focus_terms.append(cleaned)

    return ResearchPacket(
        query=primary.query or supplement.query,
        focus_terms=focus_terms,
        sources=sources,
        candidate_statements=statements,
    )


async def phase_gather(
    topic: str,
    memory: DreamMemory,
    cloud: CloudAgent,
    medium: MediumAgent,
    small: SmallAgent,
    cfg: DreamConfig,
    memory_store: Any = None,
) -> list[str]:
    logger.info("[gather] starting — topic=%s, subtopics=%s", topic, memory.subtopics[:3])
    t0 = time.perf_counter()
    packet = await _research_packet(topic, memory, cfg)
    stored_packet = _stored_research_packet(topic, memory, cfg, memory_store) if cfg.research_enabled else None
    packet = _merge_research_packets(packet, stored_packet, cfg)
    if packet:
        memory.set_research(
            query=packet.query,
            focus_terms=packet.focus_terms,
            sources=[source.as_dict() for source in packet.sources],
            candidate_statements=packet.candidate_statements,
        )
    evidence = packet.evidence_block(cfg.research_max_context_chars) if packet else ""
    sourced_statements = packet.candidate_statements if packet else []
    if packet:
        logger.info(
            "[gather] reliable-source research | query=%r | sources=%d | sourced_statements=%d | domains=%s",
            packet.query,
            len(packet.sources),
            len(sourced_statements),
            ", ".join(source.domain for source in packet.sources[:4]) or "none",
        )

    if _is_local_endpoint(cfg.cloud.base_url):
        cloud_stmts = await cloud.gather(topic, memory.subtopics, evidence=evidence)
        await asyncio.sleep(cfg.local_inference_cooldown)
        medium_stmts = await medium.gather(topic, memory.subtopics, evidence=evidence)
    else:
        cloud_task = asyncio.create_task(cloud.gather(topic, memory.subtopics, evidence=evidence))
        medium_task = asyncio.create_task(medium.gather(topic, memory.subtopics, evidence=evidence))
        cloud_stmts, medium_stmts = await asyncio.gather(cloud_task, medium_task)

    await asyncio.sleep(cfg.local_inference_cooldown)
    small_stmts = await small.gather(topic, memory.subtopics, evidence=evidence)

    combined = sourced_statements + cloud_stmts + medium_stmts + small_stmts

    logger.info(
        "[gather] complete in %.1fs | cloud=%d medium=%d small=%d total=%d",
        time.perf_counter() - t0,
        len(cloud_stmts), len(medium_stmts), len(small_stmts), len(combined),
    )
    return combined


async def phase_verify(
    topic: str,
    raw_statements: list[str],
    memory: DreamMemory,
    small: SmallAgent,
    cfg: DreamConfig,
    medium: MediumAgent | None = None,
) -> tuple[list[str], list[str]]:
    logger.info("[verify] checking %d statements", len(raw_statements))
    t0 = time.perf_counter()

    seen: set[str] = set()
    unique: list[str] = []
    for s in raw_statements:
        norm = s.strip().lower()
        if norm not in seen:
            seen.add(norm)
            unique.append(s.strip())

    evidence = memory.evidence_block(cfg.research_max_context_chars) if cfg.research_enabled else ""
    coarse_results = await small.verify_statements(unique, topic, evidence=evidence)
    coarse_pass = [r["statement"] for r in coarse_results if not r["flag"] and r["score"] >= 0.4]

    if medium is not None and coarse_pass:
        results = await medium.verify_statements(coarse_pass, topic, evidence=evidence)
    else:
        coarse_pass_set = set(coarse_pass)
        results = [r for r in coarse_results if r["statement"] in coarse_pass_set]

    verified: list[str] = []
    flagged: list[str] = []

    for r in results:
        if not r["flag"] and r["score"] >= cfg.min_verify_confidence:
            verified.append(r["statement"])
        else:
            flagged.append(r["statement"])

    coarse_pass_set = set(coarse_pass)
    for r in coarse_results:
        if r["statement"] not in coarse_pass_set:
            flagged.append(r["statement"])

    n_added = memory.add_verified_statements(verified)
    memory.add_flagged(flagged)

    logger.info(
        "[verify] done in %.1fs | verified=%d flagged=%d new_to_kb=%d",
        time.perf_counter() - t0, len(verified), len(flagged), n_added,
    )
    return verified, flagged


async def phase_examine(
    topic: str,
    cycle: int,
    memory: DreamMemory,
    cloud: CloudAgent,
    medium: MediumAgent,
    small: SmallAgent,
    cfg: DreamConfig,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    kb = memory.knowledge_base
    weak = memory.weak_areas

    logger.info(
        "[examine] cycle=%d | kb_size=%d | weak_areas=%s",
        cycle, len(kb), weak,
    )
    evidence = memory.evidence_block(cfg.research_max_context_chars) if cfg.research_enabled else ""

    logger.info("[examine] cloud creating test...")
    test_data = await cloud.create_test(
        topic=topic,
        subtopics=memory.subtopics,
        knowledge_base=kb,
        n_questions=cfg.questions_per_test,
        weak_areas=weak,
        evidence=evidence,
    )

    questions: list[dict] = test_data.get("questions", [])
    answer_key: dict[str, str] = test_data.get("answer_key", {})

    if not questions:
        logger.error("[examine] cloud returned no questions — skipping cycle")
        return {"score": 0.0, "correct": 0, "total": 0, "passed": False,
                "per_question": {}, "concept_gaps": [], "error": "no questions"}

    small.store_answer_key(answer_key)
    logger.info("[examine] answer key stored in small agent (%d answers)", len(answer_key))

    await asyncio.sleep(cfg.local_inference_cooldown)
    logger.info("[examine] medium taking test (%d questions)...", len(questions))
    student_answers = await medium.take_test(
        topic=topic,
        questions=questions,
        knowledge_base=kb,
    )

    await asyncio.sleep(cfg.local_inference_cooldown)
    logger.info("[examine] small grading...")
    grade_report = await small.grade(
        topic=topic,
        questions=questions,
        student_answers=student_answers,
    )

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


async def phase_adapt(
    topic: str,
    cycle: int,
    grade_report: dict[str, Any],
    memory: DreamMemory,
    cloud: CloudAgent,
    cfg: DreamConfig,
) -> None:
    history = memory.cycle_history[-5:]

    if grade_report.get("passed"):
        memory.increment_retry()
        logger.info(
            "[adapt] passed — retry_count=%d/%d",
            memory.topic_retry_count, cfg.max_topic_retries,
        )
    else:
        evidence = memory.evidence_block(cfg.research_max_context_chars) if cfg.research_enabled else ""
        weak_areas = await cloud.analyze_gaps(topic, grade_report, history, evidence=evidence)
        memory.weak_areas = weak_areas
        logger.info("[adapt] new weak_areas: %s", weak_areas)