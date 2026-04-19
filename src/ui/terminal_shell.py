"""
Full-screen terminal shell with a permanent top status bar, scrollable output,
and an always-available bottom input.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import suppress
from datetime import datetime
from typing import Any

from prompt_toolkit.application import Application
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.document import Document
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
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
        self._elapsed_frozen: float | None = 0.0
        self._closed = False
        self._output_text = ""
        self._max_output_chars = 240_000
        self._input_queue: asyncio.Queue[str] = asyncio.Queue()
        self._output_items: list[dict[str, Any]] = []
        self._block_lookup: dict[str, dict[str, Any]] = {}
        self._block_counter = 0
        self._latest_block_id: str | None = None

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
        self.status_area = TextArea(
            text="",
            read_only=True,
            multiline=True,
            wrap_lines=False,
            height=3,
            focusable=False,
            style="class:statusbar",
        )
        self.input_header = TextArea(
            text="",
            read_only=True,
            multiline=False,
            wrap_lines=False,
            height=1,
            focusable=False,
            style="class:input-header",
        )

        body = HSplit(
            [
                self.status_area,
                Window(height=1, char=" ", style="class:divider"),
                self.output_area,
                Window(height=1, char=" ", style="class:divider"),
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
        self._refresh_chrome()

    def _build_style(self) -> Style:
        return Style.from_dict(
            {
                "statusbar": "bg:#111827 #e5e7eb",
                "divider": "bg:#1e1b4b",
                "output": "bg:#151a2d #e5e7eb",
                "input-header": "bg:#0f172a #94a3b8",
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

        @kb.add("f4")
        def _toggle_latest(_event) -> None:
            self.toggle_latest_block(reasoning_only=True)

        @kb.add("f5")
        def _toggle_all(_event) -> None:
            self.toggle_all_blocks(reasoning_only=True)

        @kb.add("right")
        def _accept_suggestion_right(event) -> None:
            if self._accept_autosuggestion():
                return
            event.current_buffer.cursor_right(count=1)

        @kb.add("c-f")
        def _accept_suggestion_ctrl_f(event) -> None:
            if self._accept_autosuggestion():
                return
            event.current_buffer.cursor_right(count=1)

        return kb

    def _accept_autosuggestion(self) -> bool:
        buffer = self.input_area.buffer
        if self.app.current_buffer is not buffer:
            return False
        suggestion = buffer.suggestion
        if suggestion and buffer.document.is_cursor_at_the_end:
            buffer.insert_text(suggestion.text)
            return True
        return False

    def _trim(self, value: str, limit: int) -> str:
        text = " ".join((value or "").split())
        if len(text) <= limit:
            return text
        return text[: limit - 3].rstrip() + "..."

    def _format_elapsed(self) -> str:
        if self._elapsed_frozen is not None:
            elapsed = self._elapsed_frozen
        elif not self._task_started_at:
            return "0s"
        else:
            elapsed = max(0.0, time.time() - self._task_started_at)
        if elapsed < 1:
            return f"{elapsed * 1000:.0f}ms"
        if elapsed < 60:
            return f"{elapsed:.1f}s"
        minutes = int(elapsed // 60)
        seconds = int(elapsed % 60)
        return f"{minutes}m {seconds:02d}s"

    def _status_text(self) -> str:
        queue_depth = self._input_queue.qsize()
        clock = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        headline = self._trim(
            f"QwenCode | {self.mode.upper()} | {self.state.upper()} | Stage {self.stage or 'ready'} | Queue {queue_depth} | Main {self.main_tokens:,} | Local {self.local_tokens:,} | {self._format_elapsed()} | {clock}",
            160,
        )
        detail = self._trim(self.detail or "Connected and ready.", 160)
        current = self._trim(f"Current: {self.current_prompt or 'Waiting for input'}", 160)
        return "\n".join([headline, detail, current])

    def _input_header_text(self) -> str:
        line = "Input | Enter send | Right/Ctrl-F accept suggestion | Ctrl-L clear | F4 latest think | F5 all think | Ctrl-D exit"
        if self.model_summary:
            line += f" | {self.model_summary}"
        return self._trim(line, 170)

    def _refresh_text_area(self, area: TextArea, text: str) -> None:
        area.buffer.set_document(
            Document(text=text, cursor_position=0),
            bypass_readonly=True,
        )

    def _refresh_chrome(self) -> None:
        self._refresh_text_area(self.status_area, self._status_text())
        self._refresh_text_area(self.input_header, self._input_header_text())

    def invalidate(self) -> None:
        self._refresh_chrome()
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
        self._output_items = []
        self._block_lookup = {}
        self._block_counter = 0
        self._latest_block_id = None
        self._set_output_text("")

    def append_output(self, text: str) -> None:
        if not text:
            return
        self.append_entry("Output", text)

    def append_entry(self, label: str, text: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self._output_items.append(
            {
                "kind": "entry",
                "label": label.upper(),
                "timestamp": stamp,
                "text": (text or "").strip(),
            }
        )
        self._rebuild_output()

    def upsert_block(
        self,
        block_id: str,
        *,
        title: str,
        text: str,
        status: str = "ready",
        collapsed: bool | None = None,
        reasoning: bool = False,
    ) -> None:
        existing = self._block_lookup.get(block_id)
        if existing is None:
            self._block_counter += 1
            existing = {
                "kind": "block",
                "block_id": block_id,
                "display_index": self._block_counter,
                "title": title,
                "text": "",
                "status": status,
                "collapsed": True if collapsed is None else bool(collapsed),
                "reasoning": reasoning,
                "timestamp": datetime.now().strftime("%H:%M:%S"),
            }
            self._block_lookup[block_id] = existing
            self._output_items.append(existing)
        else:
            existing["title"] = title
            existing["status"] = status
            if collapsed is not None:
                existing["collapsed"] = bool(collapsed)
        existing["text"] = (text or "").strip()
        existing["reasoning"] = reasoning
        self._latest_block_id = block_id
        self._rebuild_output()

    def toggle_latest_block(self, reasoning_only: bool = False) -> bool:
        blocks = [
            item for item in self._output_items
            if item.get("kind") == "block" and (not reasoning_only or item.get("reasoning"))
        ]
        if not blocks:
            return False
        target = blocks[-1]
        target["collapsed"] = not bool(target.get("collapsed", True))
        self._latest_block_id = target["block_id"]
        self._rebuild_output()
        return True

    def toggle_all_blocks(self, reasoning_only: bool = False) -> int:
        blocks = [
            item for item in self._output_items
            if item.get("kind") == "block" and (not reasoning_only or item.get("reasoning"))
        ]
        if not blocks:
            return 0
        should_expand = any(item.get("collapsed", True) for item in blocks)
        for item in blocks:
            item["collapsed"] = False if should_expand else True
        self._rebuild_output()
        return len(blocks)

    def toggle_block(self, selector: str, reasoning_only: bool = False) -> bool:
        normalized = (selector or "").strip().lower()
        if normalized in {"", "latest"}:
            return self.toggle_latest_block(reasoning_only=reasoning_only)
        if normalized == "all":
            return bool(self.toggle_all_blocks(reasoning_only=reasoning_only))
        for item in self._output_items:
            if item.get("kind") != "block":
                continue
            if reasoning_only and not item.get("reasoning"):
                continue
            if normalized == str(item.get("display_index")) or normalized == str(item.get("block_id", "")).lower():
                item["collapsed"] = not bool(item.get("collapsed", True))
                self._latest_block_id = item["block_id"]
                self._rebuild_output()
                return True
        return False

    def describe_blocks(self, reasoning_only: bool = False) -> str:
        blocks = [
            item for item in self._output_items
            if item.get("kind") == "block" and (not reasoning_only or item.get("reasoning"))
        ]
        if not blocks:
            return "No model thinking blocks available yet."
        lines = ["Model thinking blocks"]
        for item in blocks:
            state = "collapsed" if item.get("collapsed", True) else "expanded"
            lines.append(
                f"- {item['display_index']}: {item['title']} [{item.get('status', 'ready')}, {state}]"
            )
        lines.append("Use /think toggle <n>, /think toggle latest, or /think toggle all.")
        return "\n".join(lines)

    def _block_preview(self, text: str, limit: int = 180) -> str:
        preview = " ".join((text or "").split())
        if not preview:
            return "(waiting for model output)"
        if len(preview) <= limit:
            return preview
        return preview[: limit - 3].rstrip() + "..."

    def _render_output(self) -> str:
        lines: list[str] = []
        for item in self._output_items:
            if lines:
                lines.append("")
            if item.get("kind") == "entry":
                lines.append(f"{item['label']} [{item['timestamp']}]")
                if item.get("text"):
                    lines.extend((item["text"] or "").splitlines())
                continue

            marker = "[+]" if item.get("collapsed", True) else "[-]"
            lines.append(
                f"{marker} [{item['display_index']}] {item['title']} [{str(item.get('status', 'ready')).upper()}]"
            )
            text = (item.get("text") or "").strip()
            if item.get("collapsed", True):
                lines.append(f"    {self._block_preview(text)}")
            else:
                expanded_lines = text.splitlines() if text else ["(waiting for model output)"]
                lines.extend(f"    {line}" if line else "" for line in expanded_lines)
        return "\n".join(lines).strip()

    def _rebuild_output(self) -> None:
        self._set_output_text(self._render_output())

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
            self._elapsed_frozen = None
        elif state in {"completed", "failed"} and self._task_started_at:
            self._elapsed_frozen = max(0.0, time.time() - self._task_started_at)
        elif state == "idle":
            self._task_started_at = 0.0
            self._elapsed_frozen = 0.0
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
