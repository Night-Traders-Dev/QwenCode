import re
from pathlib import Path
from ui.rich_ui import console
from rich import box
from rich.console import Group
from rich.markdown import Markdown
from rich.padding import Padding
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text

# ── colour palette ────────────────────────────────────────────────────────────
C = {
    "brand":    "#5BA3F5",
    "accent":   "#A78BFA",
    "ok":       "#4ADE80",
    "warn":     "#FBBF24",
    "err":      "#F87171",
    "dim":      "#6B7280",
    "tool":     "#34D399",
    "code":     "#F59E0B",
    "thought":  "#9CA3AF",    # dim gray for thoughts
    "markdown": "#E5E7EB",   # light gray for markdown text
    "header":   "#5BA3F5",     # brand blue for headers
    "meta":     "#9CA3AF",
    "task":     "#34D399",
    "plan":     "#A78BFA",
    "debug":    "#FBBF24",
    "panel":    "#1b2330",
    "text":     "#e5e9f0",
}

VERSION = "0.0.1"


def normalize_markdown(text: str) -> str:
    """Clean extracted model output into readable markdown-ish prose."""
    text = (text or "").replace("\u00A0", " ")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"(?<!\n)(#{1,6}\s)", r"\n\1", text)
    text = re.sub(r"(?<!\n)([-*]\s)", r"\n\1", text)
    text = re.sub(r"(?<!\n)(\d+\.\s)", r"\n\1", text)
    text = re.sub(r"(?<=[A-Za-z0-9\)])(\[[^\]]+\]\([^)]+\))", r" \1", text)
    text = re.sub(r"(\[[^\]]+\]\([^)]+\))(?=[A-Za-z0-9])", r"\1 ", text)

    if "```" not in text and "\n\n" not in text and text.count("\n") < 2 and len(text) > 280:
        sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text)
        if 3 <= len(sentences) <= 24:
            text = "\n\n".join(
                " ".join(sentences[idx: idx + 2]).strip()
                for idx in range(0, len(sentences), 2)
                if " ".join(sentences[idx: idx + 2]).strip()
            )

    return text.strip()


def render_response(text: str, title: str = "Response"):
    """Render assistant output in a readable, width-aware layout."""
    cleaned = normalize_markdown(text)
    if not cleaned:
        return

    paragraphs = [part.strip() for part in cleaned.split("\n\n") if part.strip()]
    lead = ""
    body = cleaned

    if paragraphs and len(paragraphs[0]) <= 220 and not paragraphs[0].startswith(("#", "-", "*", "1.")):
        lead = paragraphs[0]
        body = "\n\n".join(paragraphs[1:]).strip()

    blocks = [Rule(title=f"[{C['brand']}]{title}[/]", style=C["brand"])]
    if lead:
        blocks.append(
            Panel(
                Text(lead, style=C["text"]),
                border_style=C["brand"],
                box=box.ROUNDED,
                padding=(0, 1),
            )
        )
    if body:
        blocks.append(Padding(Markdown(body, code_theme="monokai"), (0, 1)))

    console.print(Group(*blocks))


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

    def finish(self, render_output: bool = True):
        """Finish rendering and display final formatted output."""
        if render_output and self.answer_text:
            cleaned = self._clean_duplicates(self.answer_text)
            render_response(cleaned)
        elif render_output and self.thinking_text:
            console.print(f"[{C['dim']}]No final answer text was captured.[/]")
