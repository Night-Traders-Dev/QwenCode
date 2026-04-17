import re
from pathlib import Path
from textwrap import TextWrapper
from ui.rich_ui import console

# ── colour palette ────────────────────────────────────────────────────────────
C = {
    "brand":  "#5BA3F5",
    "accent": "#A78BFA",
    "ok":     "#4ADE80",
    "warn":   "#FBBF24",
    "err":    "#F87171",
    "dim":    "#6B7280",
    "tool":   "#34D399",
    "code":   "#F59E0B",
}

VERSION = "0.5.0"


# ── live renderer ─────────────────────────────────────────────────────────────
class LiveRenderer:
    def __init__(self):
        self.thinking_text = ""
        self.answer_text = ""
        self._thinking_printed = 0
        self._answer_printed = 0
        self._thinking_header = False
        self._thinking_done = False
        self._answer_started = False

    def reset(self):
        self.thinking_text = ""
        self.answer_text = ""
        self._thinking_printed = 0
        self._answer_printed = 0
        self._thinking_header = False
        self._thinking_done = False
        self._answer_started = False

    def _delta(self, old: str, new: str) -> str:
        if not new:
            return ""
        if new.startswith(old):
            return new[len(old):]
        return new

    def update(
        self,
        thinking_text: str = "",
        answer_text: str = "",
        thinking_done: bool = False,
    ):
        thinking_text = thinking_text or ""
        answer_text = answer_text or ""

        if thinking_text != self.thinking_text:
            delta = self._delta(self.thinking_text, thinking_text)
            if delta:
                if not self._thinking_header:
                    console.print(f"[{C['dim']}]Thinking[/]")
                    self._thinking_header = True
                console.print(delta, end="", style=C["dim"], markup=False)
            self.thinking_text = thinking_text
            self._thinking_printed = len(thinking_text)

        if thinking_done and self._thinking_header and not self._thinking_done:
            console.print(f"[{C['dim']}]Thinking completed[/]")
            self._thinking_done = True

        if answer_text != self.answer_text:
            delta = self._delta(self.answer_text, answer_text)
            if delta:
                if not self._answer_started:
                    console.print()
                    self._answer_started = True
                console.print(delta, end="", markup=False)
            self.answer_text = answer_text
            self._answer_printed = len(answer_text)

    def finish(self):
        if self.answer_text and not self.answer_text.endswith("\n"):
            console.print()
        elif self.thinking_text and not self.thinking_text.endswith("\n"):
            console.print()
