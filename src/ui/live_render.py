import re
from pathlib import Path
from textwrap import TextWrapper
from ui.rich_ui import console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

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
        self._last_answer_len = 0
        self._buffer_lines = []
        self._printed_lines = set()

    def reset(self):
        self.thinking_text = ""
        self.answer_text = ""
        self._thinking_printed = 0
        self._answer_printed = 0
        self._thinking_header = False
        self._thinking_done = False
        self._answer_started = False
        self._last_answer_len = 0
        self._buffer_lines = []
        self._printed_lines = set()

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

    def _format_paragraph(self, text: str) -> str:
        """Format a paragraph with proper line breaks."""
        # Split into lines and filter empty ones
        lines = [line.strip() for line in text.split('\n') if line.strip()]
        if not lines:
            return ""

        # Join with proper spacing
        formatted = []
        for line in lines:
            # Remove duplicate words/phrases that might appear due to extraction issues
            cleaned = re.sub(r'(\b\w+.*?)(?:\s+\1)+', r'\1', line, flags=re.IGNORECASE)
            formatted.append(cleaned)

        return '\n\n'.join(formatted)

    def _clean_duplicates(self, text: str) -> str:
        """Remove duplicate paragraphs and repeated content from extracted text."""
        if not text:
            return text

        # Split into paragraphs
        paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
        if not paragraphs:
            return text

        # Track seen paragraphs and remove duplicates
        seen = set()
        unique_paragraphs = []
        for para in paragraphs:
            # Create a normalized key for comparison
            key = re.sub(r'\s+', ' ', para.lower())
            if key not in seen:
                seen.add(key)
                unique_paragraphs.append(para)

        # Also remove repeated phrases within paragraphs
        cleaned_paragraphs = []
        for para in unique_paragraphs:
            # Remove consecutive duplicate sentences/phrases
            cleaned = re.sub(r'(.{20,}?)(?:\s+\1)+', r'\1', para, flags=re.IGNORECASE | re.DOTALL)
            cleaned_paragraphs.append(cleaned)

        return '\n\n'.join(cleaned_paragraphs)

    def update(
        self,
        thinking_text: str = "",
        answer_text: str = "",
        thinking_done: bool = False,
    ):
        thinking_text = thinking_text or ""
        answer_text = answer_text or ""

        # Track thinking state but don't print live updates
        if thinking_text and thinking_text != self.thinking_text:
            self.thinking_text = thinking_text
            self._thinking_printed = len(thinking_text)

        if thinking_done and not self._thinking_done:
            self._thinking_done = True

        # Track answer text but don't print live updates to avoid jumbled output
        if answer_text and len(answer_text) > self._last_answer_len:
            self.answer_text = answer_text
            self._last_answer_len = len(answer_text)
            self._answer_started = True

    def finish(self):
        """Finish rendering and display final formatted output."""
        # Display thinking status if present
        if self.thinking_text:
            console.print(f"\n[{C['dim']}]💭 Thinking...[/]", style=C["dim"])
            if self._thinking_done:
                console.print(f"[{C['dim']}]✓ Thinking completed[/]\n", style=C["dim"])

        # Render the complete answer as markdown for professional formatting
        if self.answer_text:
            # Clean up any duplicate content
            cleaned = self._clean_duplicates(self.answer_text)
            console.print(Markdown(cleaned))