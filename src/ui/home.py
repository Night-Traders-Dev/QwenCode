import json
from pathlib import Path
from typing import Optional

from rich import box
from rich.columns import Columns
from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from config.config import BROWSER_DATA_DIR, CONFIG_FILE, HISTORY_FILE
from tools.definitions import TOOLS
from ui.live_render import C
from ui.rich_ui import console


HOME_SECTIONS = ("home", "workspace", "models", "memory", "tools", "dream")


def _dream_snapshot(path: str = "dream_memory.json") -> dict:
    dream_path = Path(path)
    if not dream_path.exists():
        return {
            "available": False,
            "path": str(dream_path),
            "topic": "No session saved yet",
            "cycles": 0,
            "knowledge_statements": 0,
            "best_score": 0.0,
            "weak_areas": [],
            "recent_scores": [],
        }

    try:
        data = json.loads(dream_path.read_text())
    except Exception:
        return {
            "available": False,
            "path": str(dream_path),
            "topic": "Dream memory could not be read",
            "cycles": 0,
            "knowledge_statements": 0,
            "best_score": 0.0,
            "weak_areas": [],
            "recent_scores": [],
        }

    recent_scores = [
        entry.get("score", 0.0)
        for entry in data.get("cycle_history", [])[-3:]
        if isinstance(entry, dict)
    ]
    return {
        "available": True,
        "path": str(dream_path),
        "topic": data.get("topic", "Unknown topic"),
        "cycles": len(data.get("cycle_history", [])),
        "knowledge_statements": len(data.get("knowledge_base", [])),
        "best_score": float(data.get("session_best_score", 0.0) or 0.0),
        "weak_areas": data.get("weak_areas", [])[:3],
        "recent_scores": recent_scores,
    }


def _card(title: str, lines: list[str], accent: str, subtitle: str = "") -> Panel:
    body = Text()
    if subtitle:
        body.append(f"{subtitle}\n", style=C["dim"])
    for idx, line in enumerate(lines):
        body.append(line, style=C["text"])
        if idx != len(lines) - 1:
            body.append("\n")
    return Panel(
        body,
        title=f"[{accent}]{title}[/]",
        border_style=accent,
        box=box.ROUNDED,
        padding=(0, 1),
        expand=True,
    )


def _memory_lines(memory_store=None, memory_status: Optional[dict] = None) -> list[str]:
    status = memory_status or {}
    backend = status.get("backend", "not initialized")
    lines = [f"Backend: {backend}"]
    if status.get("fallback_reason"):
        lines.append(f"Fallback: {status['fallback_reason']}")
    if memory_store is not None:
        try:
            lines.append(f"Knowledge rows: {memory_store.count_knowledge_entries():,}")
        except Exception:
            pass
        try:
            session_id = getattr(memory_store, "session_id", None)
            if session_id:
                lines.append(f"Session: {session_id}")
        except Exception:
            pass
    lines.append("Open: /go memory")
    return lines


def render_home_dashboard(
    cfg: dict,
    mode: str = "browser",
    memory_store=None,
    memory_status: Optional[dict] = None,
) -> RenderableType:
    dream = _dream_snapshot()
    recent_scores = ", ".join(f"{score * 100:.0f}%" for score in dream["recent_scores"]) or "none yet"
    fast_model = cfg.get("local_fast_model") if cfg.get("local_fast_enabled", True) else "disabled"

    hero = Panel(
        Group(
            Text("QwenCode Home", style=f"bold {C['brand']}"),
            Text(
                "Navigate the app with /home and /go <section>.",
                style=C["dim"],
            ),
            Text(
                f"Current mode: {mode}    Active model: {cfg.get('model', 'unknown')}",
                style=C["text"],
            ),
        ),
        border_style=C["brand"],
        box=box.ROUNDED,
        padding=(0, 1),
    )

    cards = [
        _card(
            "Workspace",
            [
                f"cwd: {Path.cwd()}",
                f"History: {HISTORY_FILE}",
                "Open: /go workspace",
            ],
            C["brand"],
        ),
        _card(
            "Models",
            [
                f"Cloud: {cfg.get('model', 'unknown')}",
                f"Local: {cfg.get('local_model', 'disabled') if cfg.get('local_enabled', True) else 'disabled'}",
                f"Fast path: {fast_model}",
                "Open: /go models",
            ],
            C["accent"],
        ),
        _card(
            "Memory",
            _memory_lines(memory_store=memory_store, memory_status=memory_status),
            C["ok"] if (memory_status or {}).get("backend") == "postgresql" else C["warn"],
        ),
        _card(
            "Dream",
            [
                f"Topic: {dream['topic']}",
                f"Cycles: {dream['cycles']}    Best: {dream['best_score'] * 100:.0f}%",
                f"Knowledge: {dream['knowledge_statements']}    Recent: {recent_scores}",
                "Open: /go dream",
            ],
            C["tool"],
        ),
        _card(
            "Tools",
            [
                f"Available tools: {len(TOOLS)}",
                "Core file, shell, git, memory, and Dream inspection helpers",
                "Open: /go tools",
            ],
            C["code"],
        ),
        _card(
            "Quick Nav",
            [
                "/go workspace",
                "/go models",
                "/go memory",
                "/go tools",
                "/go dream",
            ],
            C["dim"],
        ),
    ]

    return Group(hero, Columns(cards, equal=True, expand=True))


def print_home_dashboard(
    cfg: dict,
    mode: str = "browser",
    memory_store=None,
    memory_status: Optional[dict] = None,
) -> None:
    console.print(render_home_dashboard(cfg, mode=mode, memory_store=memory_store, memory_status=memory_status))


def print_home_section(
    section: str,
    cfg: dict,
    memory_store=None,
    memory_status: Optional[dict] = None,
    mode: str = "browser",
) -> None:
    section = (section or "home").strip().lower()
    if section not in HOME_SECTIONS:
        console.print(
            f"[{C['warn']}]Unknown section:[/] {section}  "
            f"[{C['dim']}]Available:[/] {', '.join(HOME_SECTIONS)}"
        )
        return

    if section == "home":
        print_home_dashboard(cfg, mode=mode, memory_store=memory_store, memory_status=memory_status)
        return

    if section == "workspace":
        table = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
        table.add_column(style=C["accent"])
        table.add_column(style=C["text"], overflow="fold")
        table.add_row("Mode", mode)
        table.add_row("cwd", str(Path.cwd()))
        table.add_row("Config", str(CONFIG_FILE))
        table.add_row("History", str(HISTORY_FILE))
        table.add_row("Browser data", str(BROWSER_DATA_DIR))
        console.print(Panel(table, title=f"[{C['brand']}]Workspace[/]", border_style=C["brand"]))
        return

    if section == "models":
        table = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
        table.add_column(style=C["accent"])
        table.add_column(style=C["text"], overflow="fold")
        table.add_row("Cloud", cfg.get("model", "unknown"))
        table.add_row("Cloud endpoint", cfg.get("base_url", "unknown"))
        table.add_row("Local", cfg.get("local_model", "disabled") if cfg.get("local_enabled", True) else "disabled")
        table.add_row("Formatter", "enabled" if cfg.get("local_format_enabled", False) else "disabled")
        table.add_row("Fast local", cfg.get("local_fast_model", "disabled") if cfg.get("local_fast_enabled", True) else "disabled")
        table.add_row("Fast backend", cfg.get("local_fast_backend", "auto"))
        console.print(Panel(table, title=f"[{C['accent']}]Model Stack[/]", border_style=C["accent"]))
        return

    if section == "memory":
        table = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
        table.add_column(style=C["accent"])
        table.add_column(style=C["text"], overflow="fold")
        status = memory_status or {}
        table.add_row("Backend", status.get("backend", "not initialized"))
        table.add_row("PostgreSQL enabled", "yes" if status.get("postgres_enabled") else "no")
        if status.get("fallback_reason"):
            table.add_row("Fallback", status["fallback_reason"])
        if memory_store is not None:
            try:
                table.add_row("Knowledge rows", f"{memory_store.count_knowledge_entries():,}")
            except Exception:
                pass
            try:
                messages = memory_store.get_conversation(cfg.get("session_id", "default"), limit=5)
                table.add_row("Recent messages", str(len(messages)))
            except Exception:
                pass
        console.print(Panel(table, title=f"[{C['ok']}]Memory[/]", border_style=C["ok"]))
        return

    if section == "tools":
        table = Table(box=box.SIMPLE, show_header=True, header_style=C["brand"], padding=(0, 1))
        table.add_column("Tool", style=C["tool"])
        table.add_column("Description", style=C["text"], overflow="fold")
        for tool in TOOLS:
            fn = tool["function"]
            table.add_row(fn["name"], fn["description"])
        console.print(Panel(table, title=f"[{C['code']}]Tools[/]", border_style=C["code"]))
        return

    dream = _dream_snapshot()
    table = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
    table.add_column(style=C["accent"])
    table.add_column(style=C["text"], overflow="fold")
    table.add_row("Topic", dream["topic"])
    table.add_row("Cycles", str(dream["cycles"]))
    table.add_row("Knowledge", str(dream["knowledge_statements"]))
    table.add_row("Best score", f"{dream['best_score'] * 100:.1f}%")
    table.add_row("Weak areas", ", ".join(dream["weak_areas"]) or "none")
    table.add_row("Memory file", dream["path"])
    table.add_row("Run", 'uv run python src/run_dream.py "your topic"')
    console.print(Panel(table, title=f"[{C['tool']}]Dream[/]", border_style=C["tool"]))
