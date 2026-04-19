"""
dream/context.py - Shared Dream runtime context for models, tools, and UI.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from config.config import CONFIG_FILE, HISTORY_FILE, MEMORY_DIR


REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
DREAM_ENTRYPOINT = SRC_ROOT / "run_dream.py"
DREAM_PACKAGE_DIR = SRC_ROOT / "dream"
DREAM_MEMORY_CLASS = DREAM_PACKAGE_DIR / "memory" / "dream_memory.py"
DREAM_SESSION_FILE = DREAM_PACKAGE_DIR / "session.py"


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


def wrap_user_with_runtime_context(
    user_input: str,
    cfg: Optional[dict] = None,
    memory_store: Any = None,
    mode: str = "browser",
    cwd: str | Path | None = None,
) -> str:
    context = build_dream_system_context(cfg=cfg, memory_store=memory_store, cwd=cwd, include_schema=False)
    return (
        "QwenCode runtime context (use as operating context; do not restate it unless relevant):\n"
        + context
        + f"\n- Active mode: {mode}"
        + "\n\nUser request:\n"
        + user_input
    )
