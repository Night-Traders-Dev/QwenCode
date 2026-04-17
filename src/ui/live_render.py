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
    "text":   "#e5e9f0",
    "tool":   "#34D399",
    "code":   "#F59E0B",
    "panel":  "#1b2330",
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
        if not old:
            return new
        if new == old:
            return ""
        if new.startswith(old):
            return new[len(old):]
        # Handle case where new text is shorter (normalization changed)
        # Return the entire new text so it gets re-printed cleanly
        if len(new) < len(old):
            return new
        # If texts diverge, find common prefix and return only the new part
        # This handles cases where normalization or whitespace differs slightly
        for i in range(min(len(old), len(new)), 0, -1):
            if new.startswith(old[:i]):
                return new[i:]
        # No common prefix found, return entire new text
        return new

    def update(
        self,
        thinking_text: str = "",
        answer_text: str = "",
        thinking_done: bool = False,
    ):
        thinking_text = thinking_text or ""
        answer_text = answer_text or ""

        # Only print thinking delta if it actually changed
        if thinking_text and thinking_text != self.thinking_text:
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

        # Only print answer delta if it actually changed
        if answer_text and answer_text != self.answer_text:
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
