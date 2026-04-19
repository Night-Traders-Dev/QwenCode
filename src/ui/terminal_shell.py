"""
Full-screen terminal shell with a permanent top status bar, scrollable output,
and an always-available bottom input.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import suppress
from datetime import datetime

from prompt_toolkit.application import Application
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.document import Document
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import TextArea

from config.config import HISTORY_FILE


class TerminalShell:
    def __init__(self, mode: str = "browser", model_summary: str = ""):
        self.mode = mode
        self.model_summary = model_summary
        self.state = "idle"
        self.stage = "ready"
        self.detail = "Connected and ready."
        self.current_prompt = ""
        self.main_tokens = 0
        self.local_tokens = 0
        self._task_started_at = 0.0
        self._closed = False
        self._output_text = ""
        self._max_output_chars = 240_000
        self._input_queue: asyncio.Queue[str] = asyncio.Queue()

        self.output_area = TextArea(
            text="",
            read_only=True,
            scrollbar=True,
            wrap_lines=True,
            focus_on_click=False,
            style="class:output",
        )
        self.input_area = TextArea(
            text="",
            prompt="> ",
            multiline=False,
            wrap_lines=False,
            height=1,
            history=FileHistory(str(HISTORY_FILE)),
            auto_suggest=AutoSuggestFromHistory(),
            style="class:input-field",
        )
        self.status_window = Window(
            content=FormattedTextControl(self._status_fragments),
            height=3,
            style="class:statusbar",
        )
        self.input_header = Window(
            content=FormattedTextControl(self._input_fragments),
            height=1,
            style="class:input-header",
        )

        body = HSplit(
            [
                self.status_window,
                Window(height=1, char="─", style="class:divider"),
                self.output_area,
                Window(height=1, char="─", style="class:divider"),
                self.input_header,
                self.input_area,
            ]
        )

        self.app = Application(
            layout=Layout(body, focused_element=self.input_area),
            key_bindings=self._build_key_bindings(),
            full_screen=True,
            mouse_support=True,
            style=self._build_style(),
        )

    def _build_style(self) -> Style:
        return Style.from_dict(
            {
                "statusbar": "bg:#111827 #e5e7eb",
                "status.title": "bg:#111827 #5ba3f5 bold",
                "status.mode": "bg:#111827 #a78bfa bold",
                "status.ok": "bg:#111827 #4ade80 bold",
                "status.warn": "bg:#111827 #fbbf24 bold",
                "status.err": "bg:#111827 #f87171 bold",
                "status.meta": "bg:#111827 #9ca3af",
                "status.detail": "bg:#111827 #e5e7eb",
                "status.prompt": "bg:#111827 #cbd5e1",
                "divider": "#334155",
                "output": "bg:#151a2d #e5e7eb",
                "input-header": "bg:#0f172a #94a3b8",
                "input.label": "bg:#0f172a #5ba3f5 bold",
                "input.help": "bg:#0f172a #94a3b8",
                "input.model": "bg:#0f172a #34d399",
                "input-field": "bg:#0b1020 #e5e7eb",
            }
        )

    def _build_key_bindings(self) -> KeyBindings:
        kb = KeyBindings()

        @kb.add("enter")
        def _submit(_event) -> None:
            self.submit_current_input()

        @kb.add("c-l")
        def _clear(_event) -> None:
            self.clear_output()

        @kb.add("c-d")
        def _exit(_event) -> None:
            self.exit()

        return kb

    def _trim(self, value: str, limit: int) -> str:
        text = " ".join((value or "").split())
        if len(text) <= limit:
            return text
        return text[: limit - 3].rstrip() + "..."

    def _format_elapsed(self) -> str:
        if not self._task_started_at:
            return "0s"
        elapsed = max(0.0, time.time() - self._task_started_at)
        if elapsed < 1:
            return f"{elapsed * 1000:.0f}ms"
        if elapsed < 60:
            return f"{elapsed:.1f}s"
        minutes = int(elapsed // 60)
        seconds = int(elapsed % 60)
        return f"{minutes}m {seconds:02d}s"

    def _status_style(self) -> str:
        if self.state == "completed":
            return "class:status.ok"
        if self.state == "failed":
            return "class:status.err"
        if self.state in {"running", "auditing"}:
            return "class:status.warn"
        return "class:status.mode"

    def _status_fragments(self):
        queue_depth = self._input_queue.qsize()
        clock = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        detail = self._trim(self.detail or "Connected and ready.", 150)
        current = self._trim(self.current_prompt or "Waiting for input", 150)
        return [
            ("class:status.title", " QwenCode "),
            ("class:status.mode", f" {self.mode.upper()} "),
            (self._status_style(), f" {self.state.upper()} "),
            (
                "class:status.meta",
                f" Stage {self.stage or 'ready'} | Queue {queue_depth} | Main {self.main_tokens:,} | Local {self.local_tokens:,} | {self._format_elapsed()} | {clock}",
            ),
            ("", "\n"),
            ("class:status.detail", f" {detail}"),
            ("", "\n"),
            ("class:status.meta", " Current "),
            ("class:status.prompt", f" {current}"),
        ]

    def _input_fragments(self):
        model_text = self._trim(self.model_summary, 90) if self.model_summary else ""
        fragments = [
            ("class:input.label", " Input "),
            ("class:input.help", " Enter to send  Ctrl-L clear  Ctrl-D exit "),
        ]
        if model_text:
            fragments.extend(
                [
                    ("class:input.help", "| "),
                    ("class:input.model", model_text),
                ]
            )
        return fragments

    def invalidate(self) -> None:
        try:
            self.app.invalidate()
        except Exception:
            pass

    def _set_output_text(self, text: str) -> None:
        trimmed = text[-self._max_output_chars :]
        self._output_text = trimmed
        self.output_area.buffer.set_document(
            Document(text=trimmed, cursor_position=len(trimmed)),
            bypass_readonly=True,
        )
        self.invalidate()

    def clear_output(self) -> None:
        self._set_output_text("")

    def append_output(self, text: str) -> None:
        if not text:
            return
        normalized = text.rstrip() + "\n"
        if self._output_text:
            combined = self._output_text.rstrip() + "\n\n" + normalized
        else:
            combined = normalized
        self._set_output_text(combined)

    def append_entry(self, label: str, text: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        heading = f"{label.upper()} [{stamp}]"
        self.append_output(f"{heading}\n{text.strip()}")

    def submit_current_input(self) -> None:
        text = (self.input_area.text or "").strip()
        if not text:
            return
        try:
            self.input_area.buffer.append_to_history()
        except Exception:
            pass
        self.input_area.buffer.set_document(Document(text="", cursor_position=0), bypass_readonly=True)
        self._input_queue.put_nowait(text)
        self.append_entry("You", text)
        self.invalidate()

    async def next_input(self) -> str:
        item = await self._input_queue.get()
        self.invalidate()
        return item

    @property
    def pending_count(self) -> int:
        return self._input_queue.qsize()

    def update_status(
        self,
        *,
        state: str | None = None,
        stage: str | None = None,
        detail: str | None = None,
        current_prompt: str | None = None,
        main_tokens: int | None = None,
        local_tokens: int | None = None,
        reset_timer: bool = False,
    ) -> None:
        if state is not None:
            self.state = state
        if stage is not None:
            self.stage = stage
        if detail is not None:
            self.detail = detail
        if current_prompt is not None:
            self.current_prompt = current_prompt
        if main_tokens is not None:
            self.main_tokens = main_tokens
        if local_tokens is not None:
            self.local_tokens = local_tokens
        if reset_timer:
            self._task_started_at = time.time()
        self.invalidate()

    async def run_async(self) -> None:
        async def _ticker() -> None:
            while not self._closed:
                await asyncio.sleep(0.5)
                self.invalidate()

        ticker = asyncio.create_task(_ticker())
        try:
            await self.app.run_async()
        finally:
            self._closed = True
            ticker.cancel()
            with suppress(asyncio.CancelledError):
                await ticker

    def exit(self) -> None:
        self._closed = True
        try:
            self.app.exit()
        except Exception:
            pass
