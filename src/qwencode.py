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
from tools.definitions import TOOLS
from openai import OpenAI, APIError, APIConnectionError
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box
from browser.session import browser_session



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
DASHSCOPE_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL      = "qwen3-coder-plus"
LOCAL_BASE_URL     = "http://localhost:11434/v1"
LOCAL_API_KEY      = "ollama"

MAX_TOOL_ITERS   = 20

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
            if text and not text.endswith("\n"):
                console.print()
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
