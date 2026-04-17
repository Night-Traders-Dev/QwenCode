#!/usr/bin/env python3
"""
qwencode.py — A Claude Code-style agentic terminal harness for Qwen Coder.

Supports:
  • DashScope (Alibaba Cloud) via OpenAI-compatible API
  • Local inference: Ollama, vLLM, LM Studio
  • Browser automation mode (free tier via Qwen web UI)
  • Full agentic tool loop: read/write files, run shell, search, glob
  • Streaming responses with rich terminal rendering
  • Persistent config at ~/.qwencode/config.json

Usage:
  python qwencode.py [--model MODEL] [--base-url URL] [--api-key KEY]
  python qwencode.py --local            # shortcut for Ollama on localhost
  python qwencode.py --browser          # use browser automation (free tier)
  python qwencode.py --browser --headless  # headless after first login

Slash commands inside the session:
  /help     show this help
  /clear    clear conversation history
  /model    show or set the current model
  /tools    list available tools
  /config   show active config
  /exit     quit

Requirements:
  pip install openai rich prompt_toolkit
  For browser mode: pip install playwright && playwright install chromium
"""

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import textwrap
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# ── dependency check ──────────────────────────────────────────────────────────
_MISSING = []
try:
    from openai import OpenAI, APIError, APIConnectionError
except ImportError:
    _MISSING.append("openai")
try:
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.panel import Panel
    from rich.syntax import Syntax
    from rich.table import Table
    from rich.text import Text
    from rich import box
except ImportError:
    _MISSING.append("rich")
try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.styles import Style as PTStyle
    from prompt_toolkit.formatted_text import HTML
    from prompt_toolkit.key_binding import KeyBindings
except ImportError:
    _MISSING.append("prompt_toolkit")

if _MISSING:
    print(f"[error] Missing packages: {', '.join(_MISSING)}")
    print(f"  pip install {' '.join(_MISSING)}")
    sys.exit(1)

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

console = Console(highlight=False)

# ── browser availability (single authoritative check) ─────────────────────────
try:
    from playwright.async_api import (
        async_playwright,
        BrowserContext,
        Page,
    )
    BROWSER_AVAILABLE = True
except ImportError:
    BROWSER_AVAILABLE = False

# ── constants ─────────────────────────────────────────────────────────────────
VERSION          = "0.5.0"
CONFIG_DIR       = Path.home() / ".qwencode"
CONFIG_FILE      = CONFIG_DIR / "config.json"
HISTORY_FILE     = CONFIG_DIR / "history"
BROWSER_DATA_DIR = CONFIG_DIR / "browser_data"

DASHSCOPE_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL      = "qwen3-coder-plus"
LOCAL_BASE_URL     = "http://localhost:11434/v1"
LOCAL_API_KEY      = "ollama"

MAX_TOOL_ITERS   = 20
MAX_OUTPUT_CHARS = 8000

SYSTEM_PROMPT = """
You are Qwen Coder, an expert AI software engineer running inside a terminal.
You have access to tools that let you read and write files, run shell commands,
search for text, and list directories on the user's machine.

Guidelines:
- Think step-by-step before acting. Use tools to gather context before editing.
- Prefer minimal, precise edits. Don't rewrite files unnecessarily.
- Always show what you changed and why.
- When running shell commands, prefer non-interactive, non-destructive ones.
- Never run rm -rf, format, or other destructive commands without explicit user approval.
- Keep the user informed at each step.
- Respond in Markdown when appropriate.
"""

# ── config ────────────────────────────────────────────────────────────────────
DEFAULT_CONFIG = {
    "base_url":    DASHSCOPE_BASE_URL,
    "api_key":     "",
    "model":       DEFAULT_MODEL,
    "temperature": 0.7,
    "max_tokens":  8192,
    "stream":      True,
}

def load_config() -> dict:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_FILE.exists():
        try:
            saved = json.loads(CONFIG_FILE.read_text())
            cfg.update(saved)
        except Exception:
            pass
    for env, key in [
        ("DASHSCOPE_API_KEY", "api_key"),
        ("OPENAI_API_KEY",    "api_key"),
        ("QWEN_BASE_URL",     "base_url"),
        ("QWEN_MODEL",        "model"),
    ]:
        v = os.environ.get(env)
        if v:
            cfg[key] = v
    return cfg

def save_config(cfg: dict):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    out = {k: v for k, v in cfg.items() if k != "api_key" or v}
    CONFIG_FILE.write_text(json.dumps(out, indent=2))

# ── tool definitions ──────────────────────────────────────────────────────────
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the full contents of a file on disk.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute or relative path to the file.",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write (or overwrite) a file on disk with the given content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path":    {"type": "string", "description": "Path to the file."},
                    "content": {"type": "string", "description": "Full content to write."},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_bash",
            "description": (
                "Run a bash shell command and return stdout + stderr. "
                "Avoid destructive commands; prefer read-only or reversible operations."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string",  "description": "The shell command to run."},
                    "timeout": {"type": "integer", "description": "Seconds before timeout (default 30)."},
                    "workdir": {"type": "string",  "description": "Working directory (default: cwd)."},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "List files and directories at a given path (non-recursive by default).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path":      {"type": "string",  "description": "Directory to list."},
                    "recursive": {"type": "boolean", "description": "Recurse into subdirectories."},
                    "pattern":   {"type": "string",  "description": "Glob pattern filter, e.g. '*.py'."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "Search for a regex or literal pattern across files in a directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern":     {"type": "string",  "description": "Text or regex to search for."},
                    "directory":   {"type": "string",  "description": "Root directory to search in."},
                    "glob":        {"type": "string",  "description": "File glob to limit search, e.g. '*.py'."},
                    "max_results": {"type": "integer", "description": "Max matching lines (default 50)."},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "glob_files",
            "description": "Find files matching a glob pattern.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern":     {"type": "string",  "description": "Glob pattern, e.g. 'src/**/*.rs'."},
                    "directory":   {"type": "string",  "description": "Root directory (default: cwd)."},
                    "max_results": {"type": "integer", "description": "Max results (default 100)."},
                },
                "required": ["pattern"],
            },
        },
    },
]

# ── tool implementations ──────────────────────────────────────────────────────
def _truncate(s: str, n: int = MAX_OUTPUT_CHARS) -> str:
    if len(s) <= n:
        return s
    head = int(n * 0.6)
    tail = n - head
    return s[:head] + f"\n\n... [TRUNCATED {len(s) - n} chars] ...\n\n" + s[-tail:]

def tool_read_file(path: str) -> str:
    p = Path(path).expanduser()
    if not p.exists():
        return f"[error] File not found: {path}"
    if not p.is_file():
        return f"[error] Not a file: {path}"
    try:
        return _truncate(p.read_text(errors="replace"))
    except Exception as e:
        return f"[error] {e}"

def tool_write_file(path: str, content: str) -> str:
    p = Path(path).expanduser()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        existed = p.exists()
        p.write_text(content)
        action = "Updated" if existed else "Created"
        return f"[ok] {action} {p} ({len(content)} bytes)"
    except Exception as e:
        return f"[error] {e}"

def tool_run_bash(
    command: str,
    timeout: int = 30,
    workdir: Optional[str] = None,
) -> str:
    cwd = Path(workdir).expanduser() if workdir else Path.cwd()
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True,
            timeout=timeout, cwd=cwd,
        )
        out = result.stdout + result.stderr
        return f"[exit {result.returncode}]\n" + _truncate(out.strip())
    except subprocess.TimeoutExpired:
        return f"[error] Command timed out after {timeout}s"
    except Exception as e:
        return f"[error] {e}"

def tool_list_directory(
    path: str,
    recursive: bool = False,
    pattern: Optional[str] = None,
) -> str:
    p = Path(path).expanduser()
    if not p.exists():
        return f"[error] Path not found: {path}"
    try:
        glob = p.rglob(pattern or "*") if recursive else p.glob(pattern or "*")
        entries = sorted(glob, key=lambda x: (x.is_file(), x.name))
        lines = [
            f"  {e.relative_to(p)}{'/' if e.is_dir() else ''}"
            for e in entries
        ]
        return "\n".join(lines) if lines else "(empty)"
    except Exception as e:
        return f"[error] {e}"

def tool_search_files(
    pattern: str,
    directory: str = ".",
    glob: str = "*",
    max_results: int = 50,
) -> str:
    root = Path(directory).expanduser()
    try:
        rx = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        return f"[error] Invalid regex: {e}"
    hits = []
    try:
        for fpath in sorted(root.rglob(glob)):
            if not fpath.is_file():
                continue
            try:
                for i, line in enumerate(
                    fpath.read_text(errors="replace").splitlines(), 1
                ):
                    if rx.search(line):
                        hits.append(
                            f"{fpath.relative_to(root)}:{i}: {line.rstrip()}"
                        )
                        if len(hits) >= max_results:
                            return "\n".join(hits) + f"\n... (stopped at {max_results})"
            except Exception:
                pass
    except Exception as e:
        return f"[error] {e}"
    return "\n".join(hits) if hits else "(no matches)"

def tool_glob_files(
    pattern: str,
    directory: str = ".",
    max_results: int = 100,
) -> str:
    root = Path(directory).expanduser()
    try:
        results = sorted(root.glob(pattern))[:max_results]
        lines = [str(p.relative_to(root)) for p in results]
        suffix = f"\n... ({max_results} limit)" if len(results) == max_results else ""
        return "\n".join(lines) + suffix if lines else "(no matches)"
    except Exception as e:
        return f"[error] {e}"

TOOL_FNS = {
    "read_file":      tool_read_file,
    "write_file":     tool_write_file,
    "run_bash":       tool_run_bash,
    "list_directory": tool_list_directory,
    "search_files":   tool_search_files,
    "glob_files":     tool_glob_files,
}

def dispatch_tool(name: str, args: dict) -> str:
    fn = TOOL_FNS.get(name)
    if fn is None:
        return f"[error] Unknown tool: {name}"
    try:
        return fn(**args)
    except TypeError as e:
        return f"[error] Bad arguments for {name}: {e}"

# ── rendering ─────────────────────────────────────────────────────────────────
def print_tool_call(name: str, args: dict):
    icon = {
        "read_file": "📖", "write_file": "✏️", "run_bash": "🔧",
        "list_directory": "📁", "search_files": "🔍", "glob_files": "🗂️",
    }.get(name, "🔩")
    args_str = " ".join(f"{k}={repr(v)}" for k, v in args.items())
    console.print(
        f"  [{C['tool']}]{icon} {name}[/] [{C['dim']}]{args_str[:120]}[/]"
    )

def print_tool_result(result: str, ok: bool = True):
    col   = C["ok"] if ok else C["err"]
    lines = result.strip().splitlines()
    preview = "\n".join(lines[:6])
    if len(lines) > 6:
        preview += f"\n  [{C['dim']}]... ({len(lines)} lines total)[/]"
    console.print(f"  [{col}]└─[/] [{C['dim']}]{preview}[/]")

def render_assistant(text: str):
    fence_re = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)
    pos = 0
    for m in fence_re.finditer(text):
        before = text[pos : m.start()].strip()
        if before:
            console.print(Markdown(before))
        lang = m.group(1) or "text"
        code = m.group(2)
        console.print(
            Syntax(code, lang, theme="monokai",
                   line_numbers=len(code.splitlines()) > 10)
        )
        pos = m.end()
    tail = text[pos:].strip()
    if tail:
        console.print(Markdown(tail))

# ── streaming completion (API mode) ───────────────────────────────────────────
def stream_completion(
    client: OpenAI, cfg: dict, messages: list
) -> tuple[str, list]:
    full_text = ""
    tool_call_accum: dict[int, dict] = {}

    console.print(f"\n[{C['brand']}]◆ Qwen Coder[/] ", end="")

    with client.chat.completions.create(
        model=cfg["model"],
        messages=messages,
        tools=TOOLS,
        tool_choice="auto",
        temperature=cfg["temperature"],
        max_tokens=cfg["max_tokens"],
        stream=True,
    ) as stream:
        for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta is None:
                continue
            if delta.content:
                console.print(delta.content, end="", markup=False)
                full_text += delta.content
            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    if idx not in tool_call_accum:
                        tool_call_accum[idx] = {"id": "", "name": "", "args": ""}
                    if tc.id:
                        tool_call_accum[idx]["id"] += tc.id
                    if tc.function and tc.function.name:
                        tool_call_accum[idx]["name"] += tc.function.name
                    if tc.function and tc.function.arguments:
                        tool_call_accum[idx]["args"] += tc.function.arguments

    console.print()
    tool_calls = [
        {
            "id":        acc["id"] or f"call_{idx}",
            "name":      acc["name"],
            "args_json": acc["args"],
        }
        for idx, acc in sorted(tool_call_accum.items())
    ]
    return full_text, tool_calls

def agentic_turn_api(
    client: OpenAI, cfg: dict, messages: list
) -> list:
    for iteration in range(MAX_TOOL_ITERS):
        text, tool_calls = stream_completion(client, cfg, messages)

        if not tool_calls:
            if text.strip():
                console.print()
                render_assistant(text)
            messages.append({"role": "assistant", "content": text or ""})
            return messages

        assistant_msg: dict[str, Any] = {
            "role":    "assistant",
            "content": text or "",          # never None — some endpoints reject it
            "tool_calls": [
                {
                    "id":       tc["id"],
                    "type":     "function",
                    "function": {
                        "name":      tc["name"],
                        "arguments": tc["args_json"],
                    },
                }
                for tc in tool_calls
            ],
        }
        messages.append(assistant_msg)

        console.print(f"\n[{C['accent']}]⚙  Tools[/]")
        tool_results = []
        for tc in tool_calls:
            try:
                args = json.loads(tc["args_json"] or "{}")
            except json.JSONDecodeError:
                args = {}
            print_tool_call(tc["name"], args)
            result = dispatch_tool(tc["name"], args)
            print_tool_result(result, ok=not result.startswith("[error]"))
            tool_results.append({
                "role":         "tool",
                "tool_call_id": tc["id"],
                "content":      result,
            })
        messages.extend(tool_results)

    console.print(
        f"[{C['warn']}]⚠  Reached max tool iterations ({MAX_TOOL_ITERS})[/]"
    )
    return messages

# ── browser controller ────────────────────────────────────────────────────────

# ── cookie import utility ─────────────────────────────────────────────────────
def import_cookies_from_json(cookie_file: str, data_dir: Path):
    """
    Import cookies exported from a real browser (e.g. via Cookie-Editor
    extension) into the Playwright persistent context on next launch.

    Export steps:
      1. Log in to chat.qwen.ai in your real browser.
      2. Install the "Cookie-Editor" extension.
      3. On chat.qwen.ai, open Cookie-Editor and click Export -> JSON.
      4. Save the JSON to a file, e.g. ~/qwen_cookies.json
      5. Run:  python3 qwencode.py --import-cookies ~/qwen_cookies.json
    """
    cookies = json.loads(Path(cookie_file).read_text())
    dest = data_dir / "Default"
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / "cookies_to_inject.json"
    out.write_text(json.dumps(cookies, indent=2))
    console.print(f"[{C['ok']}]Wrote {len(cookies)} cookies to {out}[/]")
    console.print(
        f"[{C['warn']}]Run python3 qwencode.py --browser to use the imported session.[/]"
    )



# ── live renderer ─────────────────────────────────────────────────────────────
class LiveRenderer:
    def __init__(self):
        self.full_text    = ""
        self._printed     = 0
        self._think_shown = False

    def reset(self):
        self.full_text    = ""
        self._printed     = 0
        self._think_shown = False

    def _emit(self, chunk: str):
        if not chunk:
            return
        low = chunk.lower()
        if "thinking completed" in low or "thinking..." in low:
            if not self._think_shown:
                console.print(f"\n[{C['dim']}]🤔 {chunk.strip()}[/]", markup=False)
                self._think_shown = True
            return
        console.print(chunk, end="", markup=False)

    def update(self, new_text: str):
        if not new_text:
            return
        if len(new_text) < self._printed:
            self.full_text = new_text
            self._printed  = 0
        else:
            self.full_text = new_text
        delta = self.full_text[self._printed:]
        if delta:
            self._emit(delta)
            self._printed = len(self.full_text)

    def finish(self):
        remaining = self.full_text[self._printed:]
        if remaining:
            self._emit(remaining)
            self._printed = len(self.full_text)


# ── transcript mirror ─────────────────────────────────────────────────────────
class BrowserTranscriptMirror:
    """
    Snapshots the number of assistant messages BEFORE submitting,
    then waits for a NEW message to appear (count + 1) and streams
    only that message's text live to the terminal.
    """

    PROBE_JS = r"""
    () => {
        const CONTAINER_SELS = [
            '.conversation-content', '.chat-wrapper', '.chat-content',
            '.messages', '[class*="conversation"]', '[class*="chat-list"]',
            '[class*="message-list"]', 'main',
        ];
        const MSG_SELS = [
            '.message-item.assistant',
            '.message-item[data-role="assistant"]',
            '[class*="assistant"][class*="message"]',
            '[class*="message"][class*="assistant"]',
            '.msg-assistant', '.ai-message',
            '[data-role="assistant"]', '[data-type="assistant"]',
        ];

        const getText = (el) => {
            const clone = el.cloneNode(true);
            ['button','a[href]','[class*="source"]','[class*="cite"]',
             '[class*="reference"]','[class*="action"]','[class*="feedback"]']
             .forEach(s => clone.querySelectorAll(s).forEach(n => n.remove()));
            return (clone.innerText || clone.textContent || '').trim();
        };

        for (const sel of MSG_SELS) {
            const nodes = Array.from(document.querySelectorAll(sel));
            if (nodes.length > 0) {
                return {
                    count: nodes.length,
                    text: getText(nodes[nodes.length - 1]),
                    method: sel
                };
            }
        }
        for (const csel of CONTAINER_SELS) {
            const container = document.querySelector(csel);
            if (!container) continue;
            const children = Array.from(container.children);
            for (let i = children.length - 1; i >= 0; i--) {
                const t = getText(children[i]);
                if (t.length > 20) {
                    return { count: children.length, text: t,
                             method: csel + '[last-child]' };
                }
            }
        }
        return { count: 0, text: '', method: 'none' };
    }
    """

    def __init__(self, page: "Page"):
        self.page        = page
        self._pre_count  = 0

    async def snapshot(self):
        result = await self._probe()
        self._pre_count = result.get("count", 0)

    async def _probe(self) -> dict:
        try:
            return await self.page.evaluate(self.PROBE_JS) or {}
        except Exception:
            return {"count": 0, "text": "", "method": "error"}

    async def stream_new_response(
        self,
        renderer: LiveRenderer,
        timeout_ms: int = 120_000,
        poll_interval: float = 0.12,
        stable_seconds: float = 1.8,
    ) -> str:
        renderer.reset()
        start       = time.monotonic()
        last_change = start
        last_text   = ""
        found_new   = False

        while (time.monotonic() - start) * 1000 < timeout_ms:
            result = await self._probe()
            count  = result.get("count", 0)
            text   = result.get("text",  "")

            if not found_new:
                if count > self._pre_count and text:
                    found_new = True
                    console.print(
                        f"[{C['dim']}]  (selector: {result.get('method','?')})[/]"
                    )
                else:
                    await asyncio.sleep(poll_interval)
                    continue

            if text != last_text:
                renderer.update(text)
                last_text   = text
                last_change = time.monotonic()

            if text and (time.monotonic() - last_change) >= stable_seconds:
                break

            await asyncio.sleep(poll_interval)

        renderer.finish()
        return last_text


# ── browser controller ────────────────────────────────────────────────────────
class QwenBrowserController:
    SEL_TEXTAREA = "textarea"
    SEL_SEND_BTN = 'button[aria-label="Send"]'
    QWEN_CHAT_URL = "https://chat.qwen.ai/"

    RESPONSE_TIMEOUT_MS = 120_000
    LOGIN_TIMEOUT_MS = 120_000
    MAX_TOOL_ROUNDS = 20

    TOOL_CALL_CANDIDATES = [
        "[data-tool]",
        ".tool-call",
        "[data-testid*='tool']",
    ]

    def __init__(self, headless: bool = False, data_dir: Optional[Path] = None):
        self._headless = headless
        self._data_dir = str(data_dir or BROWSER_DATA_DIR)
        self._pw = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._renderer = LiveRenderer()

    async def start(self):
        self._pw = await async_playwright().start()
        base_dir = Path(self._data_dir)
        base_dir.mkdir(parents=True, exist_ok=True)

        launch_error = None

        # First try the normal persistent profile
        try:
            self._context = await self._launch_context(base_dir)
        except Exception as e:
            launch_error = e
            msg = str(e)

            if "ProcessSingleton" in msg or "SingletonLock" in msg or "profile is already in use" in msg:
                console.print(
                    f"[{C['warn']}]Profile is locked; attempting recovery...[/]"
                )

                if self._profile_seems_idle(base_dir):
                    self._cleanup_profile_locks(base_dir)
                    try:
                        self._context = await self._launch_context(base_dir)
                        launch_error = None
                    except Exception as e2:
                        launch_error = e2

                if launch_error is not None:
                    fallback_dir = self._make_fallback_profile_dir()
                    console.print(
                        f"[{C['warn']}]Using temporary browser profile:[/] {fallback_dir}"
                    )
                    self._context = await self._launch_context(fallback_dir)
            else:
                raise

        if self._context is None:
            raise launch_error or RuntimeError("Failed to launch browser context")

        pages = self._context.pages
        self._page = pages[0] if pages else await self._context.new_page()

    async def _launch_context(self, profile_dir: Path):
        return await self._pw.chromium.launch_persistent_context(
            str(profile_dir),
            headless=self._headless,
            channel="chrome",
            viewport={"width": 1280, "height": 900},
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
            ],
        )

    def _profile_seems_idle(self, profile_dir: Path) -> bool:
        try:
            result = subprocess.run(
                ["pgrep", "-af", str(profile_dir)],
                capture_output=True,
                text=True,
                timeout=3,
            )
            return result.returncode != 0
        except Exception:
            return True

    def _cleanup_profile_locks(self, profile_dir: Path):
        lock_names = [
            "SingletonLock",
            "SingletonCookie",
            "SingletonSocket",
        ]
        for name in lock_names:
            p = profile_dir / name
            if p.exists() or p.is_symlink():
                try:
                    p.unlink()
                    console.print(f"[{C['dim']}]Removed stale lock {p}[/]")
                except Exception as e:
                    console.print(f"[{C['warn']}]Could not remove {p}: {e}[/]")

    def _make_fallback_profile_dir(self) -> Path:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        fallback = Path(self._data_dir).parent / f"browser_data_run_{stamp}"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


    async def close(self):
        try:
            if self._context:
                await self._context.close()
        finally:
            if self._pw:
                await self._pw.stop()


    async def ensure_logged_in(self):
        page = self._page

        cookie_file = Path(self._data_dir) / "Default" / "cookies_to_inject.json"
        if cookie_file.exists():
            try:
                cookies = json.loads(cookie_file.read_text())
                await self._context.add_cookies(cookies)
                console.print(f"[{C['ok']}]Injected {len(cookies)} session cookies.[/]")
                cookie_file.unlink()
            except Exception as e:
                console.print(f"[{C['warn']}]Cookie injection failed: {e}[/]")

        await page.goto(self.QWEN_CHAT_URL, wait_until="domcontentloaded")
        try:
            await page.wait_for_selector(self.SEL_TEXTAREA, timeout=8_000)
            return
        except Exception:
            pass

        console.print(
            f"[{C['warn']}]⚠  Not logged in. Please complete OAuth in the browser window.[/]"
        )
        await page.wait_for_selector(self.SEL_TEXTAREA, timeout=self.LOGIN_TIMEOUT_MS)

    async def send_prompt_and_get_response(
        self, prompt: str
    ) -> tuple[str, list[tuple[str, dict, str]]]:
        page = self._page
        tool_history: list[tuple[str, dict, str]] = []

        mirror = BrowserTranscriptMirror(page)
        await mirror.snapshot()
        await self._submit(page, prompt)

        for _round_idx in range(self.MAX_TOOL_ROUNDS):
            final_text = await mirror.stream_new_response(
                self._renderer,
                timeout_ms=self.RESPONSE_TIMEOUT_MS,
            )
            console.print()

            pending = await self._collect_pending_tool_calls(page)
            if not pending:
                return final_text, tool_history

            console.print(f"\n[{C['accent']}]⚙  Tools (browser mode)[/]")
            result_parts = []
            for node, tool_name, args in pending:
                print_tool_call(tool_name, args)
                result = dispatch_tool(tool_name, args)
                print_tool_result(result, ok=not result.startswith("[error]"))
                tool_history.append((tool_name, args, result))
                result_parts.append(f"Tool `{tool_name}` result:\n```\n{result}\n```")
                try:
                    await node.evaluate("el => el.setAttribute('data-result-sent', '1')")
                except Exception:
                    pass

            console.print(f"\n[{C['brand']}]◆ Qwen Coder (browser)[/] ", end="")
            mirror = BrowserTranscriptMirror(page)
            await mirror.snapshot()
            await self._submit(page, "\n\n".join(result_parts))

        console.print(
            f"[{C['warn']}]⚠  Reached max tool rounds ({self.MAX_TOOL_ROUNDS}) in browser mode.[/]"
        )
        return self._renderer.full_text, tool_history
    

    async def _submit(self, page: "Page", text: str):
        textarea = await page.wait_for_selector(self.SEL_TEXTAREA, timeout=10_000)
        await textarea.fill(text)
        try:
            btn = await page.wait_for_selector(self.SEL_SEND_BTN, timeout=3_000)
            await btn.click()
        except Exception:
            await textarea.press("Enter")

    async def _collect_pending_tool_calls(
        self, page: "Page"
    ) -> list[tuple[Any, str, dict]]:
        pending = []
        for sel in self.TOOL_CALL_CANDIDATES:
            try:
                nodes = await page.query_selector_all(sel)
            except Exception:
                continue

            for node in nodes:
                try:
                    if await node.get_attribute("data-result-sent"):
                        continue
                    tool_name = await node.get_attribute("data-tool") or "unknown"
                    raw_args = (await node.text_content() or "").strip()
                    try:
                        args = json.loads(raw_args)
                    except json.JSONDecodeError:
                        args = {"raw": raw_args}
                    pending.append((node, tool_name, args))
                except Exception:
                    continue

            if pending:
                break

        return pending


# ── browser session ───────────────────────────────────────────────────────────
async def browser_session(cfg: dict, headless: bool = False):
    controller = QwenBrowserController(
        headless=headless,
        data_dir=BROWSER_DATA_DIR,
    )
    await controller.start()

    try:
        await controller.ensure_logged_in()
        print_banner_browser(cfg)
        session = build_prompt_session()

        while True:
            cwd = str(Path.cwd())
            raw = await get_input_async(session, cwd)
            if raw is None:
                break

            user_input = raw.strip()
            if not user_input:
                continue

            if user_input.startswith("/"):
                ok, _ = handle_slash(user_input, cfg, [])
                if not ok:
                    break
                continue

            console.print()
            console.print(f"[{C['brand']}]◆ Qwen Coder (browser)[/] ", end="")
            await controller.send_prompt_and_get_response(user_input)
            console.print()

    finally:
        await controller.close()
        console.print(f"[{C['dim']}]Browser closed. Bye![/]")




# ── banners ───────────────────────────────────────────────────────────────────
def print_banner(cfg: dict):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    console.print(Panel(
        f"[bold {C['brand']}]Qwen Coder Harness[/]  [dim]v{VERSION}[/]\n"
        f"[{C['dim']}]Model:[/] [{C['accent']}]{cfg['model']}[/]   "
        f"[{C['dim']}]Endpoint:[/] [{C['dim']}]{cfg['base_url']}[/]\n"
        f"[{C['dim']}]cwd:[/] {Path.cwd()}   [{C['dim']}]{ts}[/]\n\n"
        f"[{C['dim']}]Type /help for commands. Ctrl-D or /exit to quit.[/]",
        box=box.ROUNDED,
        border_style=C["brand"],
    ))

def print_banner_browser(cfg: dict):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    console.print(Panel(
        f"[bold {C['brand']}]Qwen Coder Harness (Browser Mode)[/]  [dim]v{VERSION}[/]\n"
        f"[{C['dim']}]Model:[/] [{C['accent']}]qwen-coder (web)[/]   "
        f"[{C['dim']}]Free tier via OAuth[/]\n"
        f"[{C['dim']}]cwd:[/] {Path.cwd()}   [{C['dim']}]{ts}[/]\n\n"
        f"[{C['dim']}]Type /help for commands. Ctrl-D or /exit to quit.[/]",
        box=box.ROUNDED,
        border_style=C["brand"],
    ))

# ── prompt session ────────────────────────────────────────────────────────────
def print_help():
    t = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
    t.add_column(style=C["accent"])
    t.add_column(style=C["dim"])
    for k, v in [
        ("/help",              "Show this help"),
        ("/clear",             "Clear conversation history"),
        ("/model [name]",      "Show or change the active model"),
        ("/tools",             "List available tools"),
        ("/config",            "Show active configuration"),
        ("/exit",              "Quit the session"),
        ("Ctrl-D",             "Quit"),
        ("Ctrl-C",             "Cancel current input"),
        ("",                   ""),
        ("Multiline input:",   "End with a blank line, or use Alt-Enter"),
    ]:
        t.add_row(k, v)
    console.print(Panel(t, title="Commands", border_style=C["dim"]))

def handle_slash(
    cmd: str, cfg: dict, messages: list
) -> tuple[bool, list]:
    parts = cmd.strip().split(maxsplit=1)
    verb  = parts[0].lower()
    arg   = parts[1].strip() if len(parts) > 1 else ""

    if verb == "/exit":
        return False, messages

    if verb == "/help":
        print_help()
    elif verb == "/clear":
        console.print(f"[{C['ok']}]History cleared.[/]")
        return True, []
    elif verb == "/model":
        if arg:
            cfg["model"] = arg
            save_config(cfg)
            console.print(
                f"[{C['ok']}]Model set to[/] [{C['accent']}]{arg}[/]"
            )
        else:
            console.print(
                f"[{C['dim']}]Current model:[/] [{C['accent']}]{cfg['model']}[/]"
            )
    elif verb == "/tools":
        t = Table(box=box.SIMPLE, show_header=True,
                  header_style=C["brand"])
        t.add_column("Tool",        style=C["tool"])
        t.add_column("Description", style=C["dim"])
        for tool in TOOLS:
            fn = tool["function"]
            t.add_row(fn["name"], fn["description"][:80])
        console.print(t)
    elif verb == "/config":
        safe = {
            k: ("***" if k == "api_key" and v else v)
            for k, v in cfg.items()
        }
        console.print(
            Panel(json.dumps(safe, indent=2), title="Config",
                  border_style=C["dim"])
        )
    else:
        console.print(
            f"[{C['warn']}]Unknown command: {verb}[/]  (try /help)"
        )

    return True, messages

def build_prompt_session() -> PromptSession:
    hist  = FileHistory(str(HISTORY_FILE))
    style = PTStyle.from_dict({
        "prompt":       f"bold {C['brand']}",
        "prompt-arrow": C["dim"],
    })
    kb = KeyBindings()

    @kb.add("escape", "enter")
    def _newline(event):
        event.current_buffer.insert_text("\n")

    return PromptSession(history=hist, style=style, key_bindings=kb,
                         multiline=False)

# ── get_input: sync version (API mode) ───────────────────────────────────────
def get_input(session: PromptSession, cwd: str) -> Optional[str]:
    prompt_html = HTML(
        f'<ansi fg="{C["brand"]}"><b>╭─[</b></ansi>'
        f'<ansi fg="{C["dim"]}">{cwd}</ansi>'
        f'<ansi fg="{C["brand"]}"><b>]</b></ansi>\n'
        f'<ansi fg="{C["brand"]}"><b>╰─❯ </b></ansi>'
    )
    try:
        return session.prompt(prompt_html)
    except EOFError:
        return None
    except KeyboardInterrupt:
        return ""


# ── get_input_async: async version (browser mode) ────────────────────────────
async def get_input_async(session: PromptSession, cwd: str) -> Optional[str]:
    prompt_html = HTML(
        f'<ansi fg="{C["brand"]}"><b>╭─[</b></ansi>'
        f'<ansi fg="{C["dim"]}">{cwd}</ansi>'
        f'<ansi fg="{C["brand"]}"><b>]</b></ansi>\n'
        f'<ansi fg="{C["brand"]}"><b>╰─❯ </b></ansi>'
    )
    try:
        return await session.prompt_async(prompt_html)
    except EOFError:
        return None
    except KeyboardInterrupt:
        return ""


# ── API client ────────────────────────────────────────────────────────────────
def make_client(cfg: dict) -> OpenAI:
    return OpenAI(
        base_url=cfg["base_url"],
        api_key=cfg.get("api_key") or "none",
    )

# ── arg parsing ───────────────────────────────────────────────────────────────
def parse_args():
    p = argparse.ArgumentParser(description="Qwen Coder terminal harness")
    p.add_argument("--model",       "-m", help="Model name")
    p.add_argument("--base-url",    "-u", help="API base URL")
    p.add_argument("--api-key",     "-k", help="API key")
    p.add_argument("--local",       action="store_true",
                   help=f"Use local Ollama ({LOCAL_BASE_URL})")
    p.add_argument("--browser",     action="store_true",
                   help="Use browser automation (free tier via Qwen web UI)")
    p.add_argument(
    "--import-cookies",
    metavar="FILE",
    help="Import cookies from a JSON file and exit",)

    p.add_argument("--headless",    action="store_true",
                   help="Run browser headlessly (requires prior login)")
    p.add_argument("--temperature", type=float, help="Sampling temperature")
    p.add_argument("--max-tokens",  type=int,   help="Max output tokens")
    p.add_argument("--no-stream",   action="store_true",
                   help="Disable streaming")
    return p.parse_args()

# ── main ──────────────────────────────────────────────────────────────────────
def main():
    args = parse_args()
    cfg  = load_config()

    # ── CLI overrides ─────────────────────────────────────────────────────────
    if args.local:
        cfg["base_url"] = LOCAL_BASE_URL
        cfg["api_key"]  = LOCAL_API_KEY
        if not args.model:
            cfg["model"] = "qwen2.5-coder:7b"
    if args.model:       cfg["model"]       = args.model
    if args.base_url:    cfg["base_url"]    = args.base_url
    if args.api_key:
        cfg["api_key"] = args.api_key
        save_config(cfg)   # persist an explicitly supplied key
    if args.temperature: cfg["temperature"] = args.temperature
    if args.max_tokens:  cfg["max_tokens"]  = args.max_tokens
    if args.no_stream:   cfg["stream"]      = False

    # ── import-cookies shortcut (exits immediately) ───────────────────────────
    if getattr(args, "import_cookies", None):
        import_cookies_from_json(args.import_cookies, BROWSER_DATA_DIR)
        sys.exit(0)

    # ── browser mode ──────────────────────────────────────────────────────────
    if args.browser:
        if not BROWSER_AVAILABLE:
            console.print(
                f"[{C['err']}]Playwright not installed.[/]\n"
                "  pip install playwright && playwright install chromium"
            )
            sys.exit(1)
        asyncio.run(browser_session(cfg, headless=args.headless))
        return

    # ── API mode ──────────────────────────────────────────────────────────────
    if not cfg.get("api_key") and "dashscope" in cfg["base_url"]:
        console.print(
            f"[{C['warn']}]⚠  No API key found.[/]\n"
            "  Set [bold]DASHSCOPE_API_KEY[/] env var or pass [bold]--api-key[/].\n"
            "  Get a key at https://dashscope.aliyuncs.com/\n"
            "  or use [bold]--local[/] for Ollama, "
            "or [bold]--browser[/] for free tier."
        )
        sys.exit(1)

    client   = make_client(cfg)
    messages: list = [{"role": "system", "content": SYSTEM_PROMPT}]
    session  = build_prompt_session()

    print_banner(cfg)

    while True:
        cwd = str(Path.cwd())
        raw = get_input(session, cwd)

        if raw is None:
            console.print(f"\n[{C['dim']}]Bye![/]")
            break

        user_input = raw.strip()
        if not user_input:
            continue

        if user_input.startswith("/"):
            ok, messages = handle_slash(user_input, cfg, messages)
            if not ok:
                console.print(f"[{C['dim']}]Bye![/]")
                break
            continue

        messages.append({"role": "user", "content": user_input})
        console.print()

        try:
            messages = agentic_turn_api(client, cfg, messages)
        except APIConnectionError as e:
            console.print(f"[{C['err']}]Connection error:[/] {e}")
            messages.pop()
        except APIError as e:
            console.print(f"[{C['err']}]API error {e.status_code}:[/] {e.message}")
            messages.pop()
        except KeyboardInterrupt:
            console.print(f"\n[{C['warn']}]Interrupted.[/]")
            if messages and messages[-1]["role"] == "user":
                messages.pop()

        console.print()


if __name__ == "__main__":
    main()
