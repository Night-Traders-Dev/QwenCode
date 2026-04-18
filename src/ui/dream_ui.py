import time
from collections import deque
from typing import Optional

from rich import box
from rich.columns import Columns
from rich.console import Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ui.live_render import C
from ui.rich_ui import console


class DreamLiveUI:
    PHASES = ("Gather", "Verify", "Examine", "Adapt")

    def __init__(self, topic: str, cfg) -> None:
        self.topic = topic
        self.cfg = cfg
        self.started_at = time.time()
        self.cycle = 0
        self.remaining_hours = cfg.target_duration_hours
        self.current_phase = "Preparing"
        self.phase_detail = "Booting Dream session"
        self.status = "starting"
        self.subtopics: list[str] = []
        self.weak_areas: list[str] = []
        self.recent_scores: list[float] = []
        self.best_score = 0.0
        self.knowledge_size = 0
        self.flagged_count = 0
        self.last_score: Optional[float] = None
        self.memory_backend = cfg.memory_backend
        self.session_id = cfg.session_id
        self.phase_state = {phase: "pending" for phase in self.PHASES}
        self.phase_notes = {phase: "" for phase in self.PHASES}
        self.events: deque[tuple[str, str]] = deque(maxlen=8)
        self._live: Optional[Live] = None

    def __rich__(self):
        return self._render()

    def start(self) -> None:
        if self._live:
            return
        self._live = Live(
            self,
            console=console,
            refresh_per_second=4,
            auto_refresh=False,
            transient=False,
        )
        self._live.start()
        self.refresh()

    def stop(self) -> None:
        if not self._live:
            return
        try:
            self._live.stop()
        finally:
            self._live = None

    def refresh(self) -> None:
        if self._live:
            self._live.refresh()

    def set_backend(self, backend: str) -> None:
        self.memory_backend = backend or self.memory_backend
        self.refresh()

    def set_subtopics(self, subtopics: list[str]) -> None:
        self.subtopics = list(subtopics or [])[:6]
        self.refresh()

    def start_cycle(self, cycle: int, remaining_hours: float, memory) -> None:
        self.cycle = cycle
        self.remaining_hours = max(0.0, remaining_hours)
        self.status = "running"
        self.current_phase = "Gather"
        self.phase_detail = "Collecting candidate knowledge"
        self.phase_state = {
            phase: ("running" if phase == "Gather" else "pending")
            for phase in self.PHASES
        }
        self.phase_notes = {phase: "" for phase in self.PHASES}
        self.update_memory(memory)
        self.add_event(f"Cycle {cycle} started.", C["accent"])
        self.refresh()

    def set_phase(self, phase: str, state: str, detail: str = "") -> None:
        if phase not in self.phase_state:
            return
        self.current_phase = phase
        self.phase_detail = detail or self.phase_detail
        self.phase_state[phase] = state
        if detail:
            self.phase_notes[phase] = detail
        if state == "running":
            for name in self.PHASES:
                if self.PHASES.index(name) > self.PHASES.index(phase) and self.phase_state[name] == "pending":
                    self.phase_notes[name] = ""
        self.refresh()

    def complete_phase(self, phase: str, detail: str = "") -> None:
        self.set_phase(phase, "done", detail)
        if detail:
            self.add_event(detail, C["ok"])

    def fail_phase(self, phase: str, detail: str) -> None:
        self.status = "failed"
        self.set_phase(phase, "failed", detail)
        self.add_event(detail, C["err"])

    def update_memory(self, memory, grade_report: Optional[dict] = None) -> None:
        summary = memory.summary()
        self.knowledge_size = summary["knowledge_statements"]
        self.flagged_count = summary["flagged_statements"]
        self.best_score = summary["best_score"]
        self.recent_scores = summary["recent_scores"]
        self.weak_areas = summary["weak_areas"]
        if grade_report is not None:
            self.last_score = grade_report.get("score")
        self.refresh()

    def add_event(self, text: str, color: str = C["dim"]) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.events.appendleft((stamp, f"[{color}]{text}[/]"))
        self.refresh()

    def finish(self, summary: dict, elapsed_hours: float) -> None:
        self.status = "complete"
        self.current_phase = "Complete"
        self.phase_detail = "Dream session finished"
        self.knowledge_size = summary["knowledge_statements"]
        self.flagged_count = summary["flagged_statements"]
        self.best_score = summary["best_score"]
        self.recent_scores = summary["recent_scores"]
        self.weak_areas = summary["weak_areas"]
        self.add_event(
            f"Finished in {elapsed_hours:.2f}h with best score {summary['best_score'] * 100:.1f}%.",
            C["ok"],
        )
        self.refresh()

    def _render(self):
        header = Panel(
            Group(
                Text("Dream Session", style=f"bold {C['brand']}"),
                Text(self.topic, style=f"bold {C['text']}"),
                Text(
                    f"Cloud {self.cfg.cloud.name}    Medium {self.cfg.medium.name}    Small {self.cfg.small.name}",
                    style=C["dim"],
                ),
            ),
            border_style=C["brand"],
            box=box.ROUNDED,
            padding=(0, 1),
        )

        stats = Table.grid(expand=True)
        for _ in range(4):
            stats.add_column(ratio=1)
        stats.add_row(
            self._metric("Status", self.status.upper(), C["brand"]),
            self._metric("Cycle", str(self.cycle), C["accent"]),
            self._metric("Phase", self.current_phase, C["tool"]),
            self._metric("Memory", self.memory_backend, C["ok"] if self.memory_backend == "postgresql" else C["warn"]),
        )
        stats.add_row(
            self._metric("Elapsed", self._elapsed_text(), C["text"]),
            self._metric("Remaining", f"{self.remaining_hours:.2f}h", C["text"]),
            self._metric("Knowledge", str(self.knowledge_size), C["code"]),
            self._metric("Best", f"{self.best_score * 100:.1f}%", C["ok"]),
        )

        phases = Table(box=box.SIMPLE, show_header=True, header_style=C["brand"], expand=True)
        phases.add_column("Phase", style=C["accent"], no_wrap=True)
        phases.add_column("State", style=C["text"], no_wrap=True)
        phases.add_column("Detail", style=C["dim"], overflow="fold")
        for phase in self.PHASES:
            phases.add_row(
                phase,
                self._phase_badge(self.phase_state.get(phase, "pending")),
                self.phase_notes.get(phase, "") or ("Active" if self.current_phase == phase else ""),
            )

        focus = Panel(
            Group(
                Text(f"Session ID: {self.session_id}", style=C["dim"]),
                Text(f"Detail: {self.phase_detail}", style=C["text"]),
                Text(
                    "Subtopics: " + (", ".join(self.subtopics) or "preparing"),
                    style=C["dim"],
                ),
                Text(
                    "Weak areas: " + (", ".join(self.weak_areas[:4]) or "none"),
                    style=C["dim"],
                ),
                Text(
                    "Recent scores: " + (", ".join(f"{score * 100:.0f}%" for score in self.recent_scores[-5:]) or "none"),
                    style=C["dim"],
                ),
            ),
            title=f"[{C['tool']}]Learning Focus[/]",
            border_style=C["tool"],
            box=box.ROUNDED,
            padding=(0, 1),
        )

        activity = Table(box=box.SIMPLE, show_header=True, header_style=C["brand"], expand=True)
        activity.add_column("Time", width=8, style=C["dim"], no_wrap=True)
        activity.add_column("Activity", style=C["text"], overflow="fold")
        if self.events:
            for stamp, entry in list(self.events):
                activity.add_row(stamp, Text.from_markup(entry))
        else:
            activity.add_row("--:--:--", Text("Waiting for first event", style=C["dim"]))

        return Group(
            header,
            Panel(stats, border_style=C["dim"], box=box.ROUNDED, padding=(0, 1)),
            Columns(
                [
                    Panel(phases, title=f"[{C['accent']}]Phase Progress[/]", border_style=C["accent"], box=box.ROUNDED),
                    focus,
                ],
                equal=True,
                expand=True,
            ),
            Panel(activity, title=f"[{C['code']}]Recent Activity[/]", border_style=C["code"], box=box.ROUNDED),
        )

    def _elapsed_text(self) -> str:
        elapsed = max(0.0, time.time() - self.started_at)
        if elapsed < 60:
            return f"{elapsed:.1f}s"
        mins = int(elapsed // 60)
        secs = int(elapsed % 60)
        return f"{mins}m {secs:02d}s"

    @staticmethod
    def _metric(label: str, value: str, value_style: str) -> Text:
        return Text.assemble(
            (f"{label}\n", C["dim"]),
            (value, value_style),
        )

    @staticmethod
    def _phase_badge(state: str) -> Text:
        mapping = {
            "pending": ("PENDING", C["dim"]),
            "running": ("RUNNING", C["accent"]),
            "done": ("DONE", C["ok"]),
            "failed": ("FAILED", C["err"]),
        }
        label, color = mapping.get(state, (state.upper(), C["dim"]))
        return Text(label, style=f"bold {color}")
