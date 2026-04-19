"""
dream/context.py - Shared Dream runtime context for models, tools, and UI.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from config.config import CONFIG_FILE, HISTORY_FILE, MEMORY_DIR


REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
DREAM_ENTRYPOINT = SRC_ROOT / "run_dream.py"
DREAM_PACKAGE_DIR = SRC_ROOT / "dream"
DREAM_MEMORY_CLASS = DREAM_PACKAGE_DIR / "memory" / "dream_memory.py"
DREAM_SESSION_FILE = DREAM_PACKAGE_DIR / "session.py"
RESEARCH_HEAVY_MARKERS = (
    "research",
    "source",
    "citation",
    "evidence",
    "explain",
    "analyze",
    "compare",
    "forecast",
    "weather",
    "trend",
    "study",
    "learn",
    "latest",
    "what is",
    "how does",
    "why does",
    "tell me about",
    "deep dive",
    "summary",
)


def _sorted_unique_paths(paths: list[Path], limit: int) -> list[Path]:
    deduped: list[Path] = []
    seen: set[Path] = set()
    for path in sorted(
        (item.resolve() for item in paths if item.exists() and item.is_file()),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    ):
        if path in seen:
            continue
        seen.add(path)
        deduped.append(path)
        if len(deduped) >= limit:
            break
    return deduped


def _read_dream_snapshot(path: Optional[Path]) -> dict[str, Any]:
    if path is None or not path.exists():
        return {
            "available": False,
            "topic": "No session saved yet",
            "cycles": 0,
            "knowledge_statements": 0,
            "best_score": 0.0,
            "weak_areas": [],
            "research_sources": 0,
        }

    try:
        data = json.loads(path.read_text())
    except Exception:
        return {
            "available": False,
            "topic": "Dream memory could not be read",
            "cycles": 0,
            "knowledge_statements": 0,
            "best_score": 0.0,
            "weak_areas": [],
            "research_sources": 0,
        }

    return {
        "available": True,
        "topic": data.get("topic", "Unknown topic"),
        "cycles": len(data.get("cycle_history", [])),
        "knowledge_statements": len(data.get("knowledge_base", [])),
        "best_score": float(data.get("session_best_score", 0.0) or 0.0),
        "weak_areas": [str(item) for item in data.get("weak_areas", [])[:3]],
        "research_sources": len((data.get("current_research", {}) or {}).get("sources", []) or []),
    }


def discover_dream_assets(cwd: str | Path | None = None, limit: int = 6) -> dict[str, Any]:
    root = Path(cwd or Path.cwd()).resolve()

    memory_matches = _sorted_unique_paths(
        [
            *root.glob("dream_memory.json"),
            *root.glob("dream_*.json"),
            *root.glob("dream*.json"),
        ],
        limit=limit,
    )
    log_matches = _sorted_unique_paths(
        [
            *root.glob("dream.log"),
            *root.glob("dream_*.log"),
            *root.glob("dream*.log"),
        ],
        limit=limit,
    )

    latest_memory = memory_matches[0] if memory_matches else (root / "dream_memory.json")
    latest_log = log_matches[0] if log_matches else (root / "dream.log")
    snapshot = _read_dream_snapshot(latest_memory if latest_memory.exists() else None)

    return {
        "cwd": root,
        "default_memory": root / "dream_memory.json",
        "default_log": root / "dream.log",
        "memory_files": memory_matches,
        "log_files": log_matches,
        "latest_memory": latest_memory,
        "latest_log": latest_log,
        "snapshot": snapshot,
        "entrypoint": DREAM_ENTRYPOINT,
        "package_dir": DREAM_PACKAGE_DIR,
        "session_file": DREAM_SESSION_FILE,
        "memory_class_file": DREAM_MEMORY_CLASS,
        "config_file": CONFIG_FILE,
        "history_file": HISTORY_FILE,
        "memory_dir": MEMORY_DIR,
    }


def build_dream_system_context(
    cfg: Optional[dict] = None,
    memory_store: Any = None,
    cwd: str | Path | None = None,
    include_schema: bool = True,
) -> str:
    assets = discover_dream_assets(cwd=cwd)
    snapshot = assets["snapshot"]
    lines = [
        f"- Workspace cwd: {assets['cwd']}",
        f"- Dream entrypoint: {assets['entrypoint']}",
        f"- Dream package directory: {assets['package_dir']}",
        f"- Default Dream memory JSON: {assets['default_memory']}",
        f"- Default Dream log: {assets['default_log']}",
        "- Dream topic-specific memory files often use names like dream_<topic>.json.",
        "- Dream runs a 4-phase loop: Gather -> Verify -> Examine -> Adapt.",
    ]
    if include_schema:
        lines.extend(
            [
                "- Dream memory JSON is pretty-printed and includes: topic, subtopics, knowledge_base, current_research, research_history, reinforcement, flagged_statements, cycle_history, weak_areas, topic_retry_count, session_best_score.",
                "- current_research contains: query, focus_terms, sources[{title,url,domain,snippet,query}], candidate_statements, timestamp.",
                "- reinforcement contains: concept_mastery, source_rewards, history.",
                "- Dream logs are timestamped line-oriented text logs.",
            ]
        )
    lines.extend(
        [
            "- PostgreSQL Dream categories: dream_summary, dream_cycle, dream_knowledge, dream_source.",
            "- Best Dream tools: list_dream_assets to locate artifacts, inspect_dream_memory for JSON state, search_knowledge for durable Dream rows, and read_file/read_file_chunk for dream.log or Dream code.",
        ]
    )

    if snapshot["available"]:
        lines.extend(
            [
                f"- Latest Dream memory file: {assets['latest_memory']}",
                f"- Latest Dream log file: {assets['latest_log']}",
                f"- Latest Dream snapshot: topic={snapshot['topic']} cycles={snapshot['cycles']} knowledge={snapshot['knowledge_statements']} best={snapshot['best_score'] * 100:.1f}% research_sources={snapshot['research_sources']}",
            ]
        )
        if snapshot["weak_areas"]:
            lines.append("- Latest Dream weak areas: " + ", ".join(snapshot["weak_areas"]))
    else:
        lines.append(f"- No readable Dream memory file detected yet; start with: uv run python {assets['entrypoint']} \"your topic\"")

    if cfg:
        lines.append(f"- Session id: {cfg.get('session_id', 'default')}")
    if memory_store is not None and hasattr(memory_store, "get_status"):
        try:
            status = memory_store.get_status()
            lines.append(f"- Memory backend: {status.get('backend', 'unknown')}")
        except Exception:
            pass

    return "\n".join(lines)


def build_runtime_system_prompt(
    base_prompt: str,
    cfg: Optional[dict] = None,
    memory_store: Any = None,
    mode: str = "api",
    cwd: str | Path | None = None,
) -> str:
    context = build_dream_system_context(cfg=cfg, memory_store=memory_store, cwd=cwd, include_schema=True)
    return (
        base_prompt.strip()
        + "\n\nRuntime context for this QwenCode session:\n"
        + context
        + f"\n- Active mode: {mode}"
    )


def is_research_heavy_prompt(user_input: str) -> bool:
    text = " ".join((user_input or "").strip().lower().split())
    if not text:
        return False
    if any(marker in text for marker in RESEARCH_HEAVY_MARKERS):
        return True
    if "?" in text and len(text.split()) >= 8:
        return True
    return len(text.split()) >= 18


def _extract_terms(text: str, limit: int = 8) -> list[str]:
    tokens = re.findall(r"[a-z0-9][a-z0-9._-]{2,}", (text or "").lower())
    seen: set[str] = set()
    terms: list[str] = []
    for token in tokens:
        if token in seen:
            continue
        seen.add(token)
        terms.append(token)
        if len(terms) >= limit:
            break
    return terms


def _build_memory_query(user_input: str) -> str:
    terms = _extract_terms(user_input)
    if terms:
        return " ".join(terms)
    return (user_input or "").strip()[:240]


def _clip(text: str, limit: int = 220) -> str:
    normalized = " ".join((text or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."


def _format_recall_row(category: str, content: str) -> str:
    if category == "dream_source":
        try:
            payload = json.loads(content)
        except Exception:
            payload = {}
        title = _clip(str(payload.get("title", "") or "Untitled source"), 90)
        domain = str(payload.get("domain", "") or "unknown")
        url = str(payload.get("url", "") or "")
        snippet = _clip(str(payload.get("snippet", "") or payload.get("summary", "") or ""), 140)
        parts = [f"{title} [{domain}]"]
        if snippet:
            parts.append(snippet)
        if url:
            parts.append(url)
        return " | ".join(parts)

    if category == "dream_summary":
        try:
            payload = json.loads(content)
        except Exception:
            payload = {}
        topic = str(payload.get("topic", "") or "Dream summary")
        summary = _clip(str(payload.get("summary", "") or content), 180)
        return f"{topic}: {summary}"

    return _clip(content, 180)


def build_dream_recall_context(
    user_input: str,
    memory_store: Any = None,
    limit_per_category: int = 2,
    max_items: int = 6,
) -> str:
    if memory_store is None or not is_research_heavy_prompt(user_input):
        return ""

    query = _build_memory_query(user_input)
    if not query:
        return ""

    categories = ("dream_knowledge", "dream_source", "dream_summary")
    recalled: list[str] = []

    for category in categories:
        try:
            rows = memory_store.search_knowledge(query, limit=limit_per_category, category=category)
        except Exception:
            rows = []

        for row in rows:
            content = str(row.get("content", "") or "").strip()
            if not content:
                continue
            recalled.append(f"- {category}: {_format_recall_row(category, content)}")
            if len(recalled) >= max_items:
                break
        if len(recalled) >= max_items:
            break

    if not recalled and hasattr(memory_store, "list_knowledge"):
        try:
            fallback_rows = memory_store.list_knowledge(category="dream_source", limit=min(limit_per_category, 2))
        except Exception:
            fallback_rows = []
        for row in fallback_rows:
            content = str(row.get("content", "") or "").strip()
            if not content:
                continue
            recalled.append(f"- dream_source: {_format_recall_row('dream_source', content)}")
            if len(recalled) >= max_items:
                break

    if not recalled:
        return ""

    return (
        "Relevant Dream memory recalled for this request:\n"
        + "\n".join(recalled)
        + "\nUse recalled Dream facts as working context. Prefer Dream source URLs when citing research-derived claims."
    )


def enrich_user_with_dream_recall(
    user_input: str,
    memory_store: Any = None,
) -> str:
    recall = build_dream_recall_context(user_input, memory_store=memory_store)
    if not recall:
        return user_input
    return recall + "\n\nUser request:\n" + user_input


def wrap_user_with_runtime_context(
    user_input: str,
    cfg: Optional[dict] = None,
    memory_store: Any = None,
    mode: str = "browser",
    cwd: str | Path | None = None,
) -> str:
    context = build_dream_system_context(cfg=cfg, memory_store=memory_store, cwd=cwd, include_schema=False)
    recall = build_dream_recall_context(user_input, memory_store=memory_store)
    recall_block = ("\n\n" + recall) if recall else ""
    return (
        "QwenCode runtime context (use as operating context; do not restate it unless relevant):\n"
        + context
        + f"\n- Active mode: {mode}"
        + recall_block
        + "\n\nUser request:\n"
        + user_input
    )
