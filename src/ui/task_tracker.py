"""
Task tracking, timing, and live status display system.

Provides:
- Non-blocking timers for task execution
- Step-level timing breakdown
- Task queue management
- Live token usage display
- Thinking UI visualization (Claude Code style)
- Bottom status panel with live metrics
"""

import asyncio
import time
from datetime import datetime
from typing import Optional, List, Dict, Any, Callable
from dataclasses import dataclass, field
from enum import Enum
from collections import deque
from threading import Thread, Event

from ui.rich_ui import console
from ui.live_render import C
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box


class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    AUDITING = "auditing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class StatusPanel:
    """Live bottom panel showing task metrics, timing, and status."""

    def __init__(self):
        self.task_start_time: float = 0
        self.current_stage: str = "idle"
        self.current_tool: str = ""
        self.tokens_main: int = 0
        self.tokens_local: int = 0
        self.step_name: str = ""
        self.step_start: float = 0
        self.is_running: bool = False
        self._live: Optional[Live] = None

    def start(self, prompt: str = ""):
        """Start the status panel."""
        self.task_start_time = time.time()
        self.is_running = True
        self.current_stage = "initializing"
        self.step_start = time.time()

        # Create and start live display
        self._live = Live(self._generate_panel(), console=console, refresh_per_second=4)
        self._live.start()

    def update(self,
               stage: str = None,
               tool: str = None,
               tokens_main: int = None,
               tokens_local: int = None,
               step: str = None):
        """Update status panel with new information."""
        if stage:
            self.current_stage = stage
        if tool is not None:
            self.current_tool = tool
        if tokens_main is not None:
            self.tokens_main = tokens_main
        if tokens_local is not None:
            self.tokens_local = tokens_local
        if step:
            self.step_name = step
            self.step_start = time.time()

        if self._live and self.is_running:
            self._live.update(self._generate_panel())

    def _get_elapsed(self) -> str:
        """Get formatted elapsed time."""
        if not self.task_start_time:
            return "0s"
        elapsed = time.time() - self.task_start_time
        if elapsed < 1.0:
            return f"{elapsed*1000:.0f}ms"
        elif elapsed < 60:
            return f"{elapsed:.1f}s"
        else:
            mins = int(elapsed // 60)
            secs = elapsed % 60
            return f"{mins}m {secs:.0f}s"

    def _get_step_elapsed(self) -> str:
        """Get formatted step elapsed time."""
        if not self.step_start:
            return "0s"
        elapsed = time.time() - self.step_start
        if elapsed < 1.0:
            return f"{elapsed*1000:.0f}ms"
        elif elapsed < 60:
            return f"{elapsed:.1f}s"
        else:
            mins = int(elapsed // 60)
            secs = elapsed % 60
            return f"{mins}m {secs:.0f}s"

    def _generate_panel(self) -> Panel:
        """Generate the status panel content."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        elapsed = self._get_elapsed()
        step_elapsed = self._get_step_elapsed()

        # Build status line
        status_parts = []

        # Stage indicator with color
        stage_colors = {
            "initializing": C["dim"],
            "planning": C["brand"],
            "processing": C["accent"],
            "auditing": C["tool"],
            "formatting": C["code"],
            "completed": C["ok"],
            "failed": C["err"],
            "idle": C["dim"]
        }
        stage_color = stage_colors.get(self.current_stage, C["dim"])
        status_parts.append(f"[{stage_color}]● {self.current_stage}[/{stage_color}]")

        # Current tool if applicable
        if self.current_tool:
            status_parts.append(f"[{C['tool']}]🔧 {self.current_tool}[/{C['tool']}]")

        # Step info
        if self.step_name:
            status_parts.append(f"[{C['dim']}]⏱ {self.step_name} ({step_elapsed})[/{C['dim']}]")

        # Token counts
        token_parts = []
        if self.tokens_main > 0:
            token_parts.append(f"Main: [bold]{self.tokens_main:,}[/]")
        if self.tokens_local > 0:
            token_parts.append(f"Local: [bold]{self.tokens_local:,}[/]")
        if token_parts:
            status_parts.append(f"[{C['code']}]📊 {' | '.join(token_parts)}[/{C['code']}]")

        # Elapsed time
        status_parts.append(f"[{C['dim']}]⏲ {elapsed}[/{C['dim']}]")

        # Current time
        status_parts.append(f"[{C['dim']}]🕐 {now}[/{C['dim']}]")

        # Join with separators
        status_text = f"  {' │ '.join(status_parts)}  "

        return Panel(
            Text.from_markup(status_text),
            title=f"[{C['brand']}]Task Status[/]",
            border_style=C["dim"],
            padding=(0, 1)
        )

    def stop(self):
        """Stop the live display."""
        self.is_running = False
        if self._live:
            self._live.stop()
            self._live = None

    def finish(self, final_stage: str = "completed"):
        """Finish and show final status."""
        self.current_stage = final_stage
        if self._live:
            self._live.update(self._generate_panel())
            time.sleep(0.5)  # Show final state briefly
            self.stop()


# Global status panel instance
_status_panel: Optional[StatusPanel] = None


def get_status_panel() -> StatusPanel:
    """Get or create the global status panel instance."""
    global _status_panel
    if _status_panel is None:
        _status_panel = StatusPanel()
    return _status_panel


def reset_status_panel():
    """Reset the status panel for a new task."""
    global _status_panel
    if _status_panel and _status_panel._live:
        _status_panel.stop()
    _status_panel = StatusPanel()


class TaskStep:
    """Represents a single step in a task."""
    name: str
    start_time: float = 0.0
    end_time: float = 0.0
    status: TaskStatus = TaskStatus.PENDING
    tokens_used: int = 0
    details: str = ""

    @property
    def duration(self) -> float:
        if self.start_time and self.end_time:
            return self.end_time - self.start_time
        elif self.start_time:
            return time.time() - self.start_time
        return 0.0

    def format_duration(self) -> str:
        d = self.duration
        if d < 1.0:
            return f"{d*1000:.0f}ms"
        elif d < 60:
            return f"{d:.1f}s"
        else:
            mins = int(d // 60)
            secs = d % 60
            return f"{mins}m {secs:.0f}s"


@dataclass
class Task:
    """Represents a complete task with steps."""
    id: str
    prompt: str
    created_at: float = field(default_factory=time.time)
    status: TaskStatus = TaskStatus.PENDING
    steps: List[TaskStep] = field(default_factory=list)
    total_tokens: int = 0
    result: str = ""
    audit_score: Optional[float] = None
    error: Optional[str] = None

    @property
    def total_duration(self) -> float:
        if not self.steps:
            return 0.0
        return sum(s.duration for s in self.steps)

    def format_summary(self) -> str:
        parts = []
        parts.append(f"[{self.status.value}]")
        if self.total_duration > 0:
            parts.append(f"{self.format_total_duration()}")
        if self.total_tokens > 0:
            parts.append(f"{self.total_tokens:,} tokens")
        if self.audit_score is not None:
            color = C["ok"] if self.audit_score >= 7 else C["warn"] if self.audit_score >= 5 else C["err"]
            parts.append(f"Audit: [{color}]{self.audit_score:.1f}/10[/]")
        return "  ".join(parts)

    def format_total_duration(self) -> str:
        d = self.total_duration
        if d < 1.0:
            return f"{d*1000:.0f}ms"
        elif d < 60:
            return f"{d:.1f}s"
        else:
            mins = int(d // 60)
            secs = d % 60
            return f"{mins}m {secs:.0f}s"


class TaskQueue:
    """Thread-safe task queue with status tracking."""

    def __init__(self, max_size: int = 10):
        self._queue: deque = deque(maxlen=max_size)
        self._current_task: Optional[Task] = None
        self._lock = asyncio.Lock()
        self._subscribers: List[Callable] = []

    async def add(self, task: Task):
        async with self._lock:
            self._queue.append(task)
            await self._notify()

    async def get_next(self) -> Optional[Task]:
        async with self._lock:
            if self._queue:
                self._current_task = self._queue.popleft()
                return self._current_task
            return None

    @property
    def current(self) -> Optional[Task]:
        return self._current_task

    @property
    def pending_count(self) -> int:
        return len(self._queue)

    def subscribe(self, callback: Callable):
        """Subscribe to task updates."""
        self._subscribers.append(callback)

    async def _notify(self):
        for cb in self._subscribers:
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb()
                else:
                    cb()
            except Exception:
                pass


class ThinkingUI:
    """Claude Code-style thinking UI with live updates."""

    SPINNERS = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def __init__(self):
        self._spinner_idx = 0
        self._last_update = 0
        self._thinking_lines: List[str] = []
        self._current_step: Optional[str] = None
        self._step_start: float = 0
        self._token_count = 0
        self._visible = False

    def _get_spinner(self) -> str:
        spinner = self.SPINNERS[self._spinner_idx]
        self._spinner_idx = (self._spinner_idx + 1) % len(self.SPINNERS)
        return spinner

    def start(self, initial_text: str = ""):
        """Start the thinking UI."""
        self._visible = True
        self._thinking_lines = []
        self._token_count = 0
        if initial_text:
            self._thinking_lines.append(initial_text)
        console.print(f"[{C['dim']}]╭─ thinking[/]")

    def update(self, text: str, step: str = None, tokens: int = None):
        """Update thinking content."""
        if not self._visible:
            return

        if step and step != self._current_step:
            if self._current_step:
                duration = time.time() - self._step_start
                console.print(f"[{C['dim']}]├─ {self._current_step} ({duration:.1f}s)[/]")
            self._current_step = step
            self._step_start = time.time()

        if tokens is not None:
            self._token_count = tokens

        # Only print new lines
        lines = text.split('\n')
        for line in lines[len(self._thinking_lines):]:
            if line.strip():
                spinner = self._get_spinner()
                console.print(f"[{C['dim']}]│ {spinner} {line}[/]", end='\r', markup=False)
        self._thinking_lines = lines

    def finish(self, final_text: str = "", audit_score: float = None):
        """Finish thinking UI and show summary."""
        if not self._visible:
            return

        self._visible = False

        # Print final step timing
        if self._current_step:
            duration = time.time() - self._step_start
            console.print(f"[{C['dim']}]├─ {self._current_step} ({duration:.1f}s)[/]")

        # Print summary
        summary_parts = []
        if self._token_count > 0:
            summary_parts.append(f"{self._token_count:,} tokens")
        if audit_score is not None:
            color = C["ok"] if audit_score >= 7 else C["warn"] if audit_score >= 5 else C["err"]
            summary_parts.append(f"Audit score: [{color}]{audit_score:.1f}/10[/]")

        if summary_parts:
            console.print(f"[{C['dim']}]╰─ completed ({', '.join(summary_parts)})[/]\n")
        else:
            console.print(f"[{C['dim']}]╰─ completed[/]\n")

    def clear_line(self):
        """Clear the current spinner line."""
        console.print(" " * 80, end='\r')


class TokenTracker:
    """Track token usage across main and local LLMs."""

    def __init__(self):
        self.main_tokens = 0
        self.local_tokens = 0
        self._last_display = 0
        self._display_interval = 0.5  # seconds

    def add_main(self, count: int):
        self.main_tokens += count

    def add_local(self, count: int):
        self.local_tokens += count

    @property
    def total(self) -> int:
        return self.main_tokens + self.local_tokens

    def should_update_display(self) -> bool:
        now = time.time()
        if now - self._last_display >= self._display_interval:
            self._last_display = now
            return True
        return False

    def format(self) -> str:
        parts = []
        if self.main_tokens > 0:
            parts.append(f"Main: {self.main_tokens:,}")
        if self.local_tokens > 0:
            parts.append(f"Local: {self.local_tokens:,}")
        if self.total > 0:
            parts.append(f"Total: {self.total:,}")
        return " | ".join(parts)


# Global instances
_task_queue: Optional[TaskQueue] = None
_token_tracker: Optional[TokenTracker] = None
_thinking_ui: Optional[ThinkingUI] = None


def get_task_queue() -> TaskQueue:
    global _task_queue
    if _task_queue is None:
        _task_queue = TaskQueue()
    return _task_queue


def get_token_tracker() -> TokenTracker:
    global _token_tracker
    if _token_tracker is None:
        _token_tracker = TokenTracker()
    return _token_tracker


def get_thinking_ui() -> ThinkingUI:
    global _thinking_ui
    if _thinking_ui is None:
        _thinking_ui = ThinkingUI()
    return _thinking_ui


def reset_trackers():
    """Reset all trackers for a new task."""
    global _token_tracker, _thinking_ui
    _token_tracker = TokenTracker()
    _thinking_ui = ThinkingUI()
    reset_status_panel()


async def run_task_with_timing(
    task: Task,
    main_func: Callable,
    audit_func: Callable = None,
    enable_audit: bool = True
) -> Task:
    """
    Run a task with full timing and optional auditing.

    Args:
        task: Task to execute
        main_func: Async function for main task (should return result string)
        audit_func: Optional async function for auditing (takes result, returns score)
        enable_audit: Whether to run audit step

    Returns:
        Completed task with timing and audit info
    """
    task.status = TaskStatus.RUNNING
    tracker = get_token_tracker()
    ui = get_thinking_ui()
    panel = get_status_panel()

    # Start status panel
    panel.start(task.prompt[:50] + "..." if len(task.prompt) > 50 else task.prompt)
    ui.start("Starting task...")

    # Step 1: Main task execution
    main_step = TaskStep(name="Processing request")
    task.steps.append(main_step)
    main_step.status = TaskStatus.RUNNING
    main_step.start_time = time.time()

    panel.update(stage="processing", step="Processing request")

    try:
        # Run main function
        result = await main_func()
        task.result = result

        main_step.end_time = time.time()
        main_step.status = TaskStatus.COMPLETED

        panel.update(stage="formatting", step="Formatting output")
        ui.update(result[:500] + "..." if len(result) > 500 else result, step="Main processing complete")

    except Exception as e:
        main_step.end_time = time.time()
        main_step.status = TaskStatus.FAILED
        task.error = str(e)
        task.status = TaskStatus.FAILED
        panel.finish("failed")
        ui.finish(audit_score=0)
        return task

    # Step 2: Audit (if enabled)
    if enable_audit and audit_func:
        audit_step = TaskStep(name="Auditing response")
        task.steps.append(audit_step)
        audit_step.status = TaskStatus.AUDITING
        audit_step.start_time = time.time()

        panel.update(stage="auditing", step="Running quality audit")
        ui.update("Running quality audit...", step="Auditing")

        try:
            audit_result = await audit_func(task.result)
            if isinstance(audit_result, dict):
                task.audit_score = audit_result.get('score', 5.0)
            elif isinstance(audit_result, (int, float)):
                task.audit_score = float(audit_result)
            else:
                task.audit_score = 5.0

            audit_step.end_time = time.time()
            audit_step.status = TaskStatus.COMPLETED

            # Update token counts in panel
            panel.update(tokens_local=tracker.local_tokens)

        except Exception as e:
            audit_step.end_time = time.time()
            audit_step.status = TaskStatus.FAILED
            # Continue even if audit fails

    task.status = TaskStatus.COMPLETED
    task.total_tokens = tracker.total

    # Update final token counts
    panel.update(tokens_main=tracker.main_tokens, tokens_local=tracker.local_tokens)

    # Finish UI components
    panel.finish("completed")
    ui.finish(audit_score=task.audit_score)

    return task