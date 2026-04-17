"""
Task tracking, timing, and live status display system.

Provides:
- Non-blocking timers for task execution
- Step-level timing breakdown
- Task queue management
- Live token usage display
- Thinking UI visualization (Claude Code style)
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


class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    AUDITING = "auditing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
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

    # Step 1: Main task execution
    main_step = TaskStep(name="Processing request")
    task.steps.append(main_step)
    main_step.status = TaskStatus.RUNNING
    main_step.start_time = time.time()

    ui.start("Starting task...")

    try:
        # Run main function
        result = await main_func()
        task.result = result

        main_step.end_time = time.time()
        main_step.status = TaskStatus.COMPLETED

        ui.update(result[:500] + "..." if len(result) > 500 else result, step="Main processing complete")

    except Exception as e:
        main_step.end_time = time.time()
        main_step.status = TaskStatus.FAILED
        task.error = str(e)
        task.status = TaskStatus.FAILED
        ui.finish(audit_score=0)
        return task

    # Step 2: Audit (if enabled)
    if enable_audit and audit_func:
        audit_step = TaskStep(name="Auditing response")
        task.steps.append(audit_step)
        audit_step.status = TaskStatus.AUDITING
        audit_step.start_time = time.time()

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

        except Exception as e:
            audit_step.end_time = time.time()
            audit_step.status = TaskStatus.FAILED
            # Continue even if audit fails

    task.status = TaskStatus.COMPLETED
    task.total_tokens = tracker.total
    ui.finish(audit_score=task.audit_score)

    return task
