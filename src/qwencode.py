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
import importlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional


def _reexec_in_project_venv_if_needed():
    """Prefer the repo virtualenv when the current interpreter lacks optional deps."""
    current = Path(sys.executable).resolve()
    repo_root = Path(__file__).resolve().parent.parent
    venv_python = repo_root / ".venv" / "bin" / "python"
    if not venv_python.exists() or current == venv_python.resolve():
        return

    for module_name in ("psycopg2",):
        try:
            importlib.import_module(module_name)
        except ImportError:
            os.execv(str(venv_python), [str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]])


_reexec_in_project_venv_if_needed()

from openai import OpenAI, APIError, APIConnectionError
from rich.panel import Panel
from rich.table import Table
from rich import box
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.styles import Style as PTStyle
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.key_binding import KeyBindings
from playwright.async_api import async_playwright, BrowserContext, Page

from tools.definitions import TOOLS
from tools.tools import dispatch_tool, print_tool_call, print_tool_result
from tools.api import agentic_turn_api
from config.config import MISSING, HISTORY_FILE, LOCAL_BASE_URL, LOCAL_API_KEY, load_config, save_config, BROWSER_DATA_DIR, MAX_TOOL_ITERS
from config.prompt import build_prompt_session, get_input, handle_slash
from browser.session import browser_session
from dream.context import build_runtime_system_prompt, enrich_user_with_dream_recall
from ui.home import print_home_dashboard
from ui.rich_ui import console
from ui.live_render import C
from ui.banner import print_banner

try:
    from memory.store import MemoryStore
except Exception:
    MemoryStore = None


# ── dependency check ──────────────────────────────────────────────────────────
if MISSING:
    print(f"[error] Missing packages: {', '.join(MISSING)}")
    print(f"  pip install {' '.join(MISSING)}")
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



BASE_SYSTEM_PROMPT = """
You are Qwen Coder, an expert AI software engineer running inside a terminal.
You have access to tools that let you read and write files, run shell commands,
search for text, and list directories on the user's machine.

You have internet access for searching and browsing.

Guidelines:
- Only respond in english.
- Always use tools for tasks like file I/O, shell commands, or searching.
- When using tools, be concise and precise with your instructions.
- For file paths, use absolute paths or paths relative to the current working directory.
- When running shell commands, ensure they are safe and non-destructive.
- Think step-by-step before acting. Use tools to gather context before editing.
- Prefer minimal, precise edits. Don't rewrite files unnecessarily.
- Always show what you changed and why.
- When running shell commands, prefer non-interactive, non-destructive ones.
- Never run rm -rf, format, or other destructive commands without explicit user approval.
- Keep the user informed at each step.
- Respond in Markdown when appropriate.
"""



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
            cfg["model"] = cfg.get("local_model") or "qwen3.5:4b"
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
    memory_store = None
    memory_status = None
    if MemoryStore is not None:
        try:
            memory_store = MemoryStore(
                db_url=cfg.get("memory_db_url") or None,
                backend=cfg.get("memory_backend", "auto"),
                require_postgres=cfg.get("require_postgres", False),
            )
            memory_status = memory_store.get_status()
            memory_store.get_or_create_session(
                cfg.get("session_id", "default"),
                model_main=cfg.get("model"),
            )
        except Exception as e:
            console.print(f"[{C['warn']}]Memory store init failed: {e}[/]")
            memory_store = None
            memory_status = None

    messages: list = []
    session  = build_prompt_session()

    print_banner(cfg)
    print_home_dashboard(
        cfg,
        mode="api",
        memory_store=memory_store,
        memory_status=memory_status,
    )

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
            ok, messages = handle_slash(
                user_input,
                cfg,
                messages,
                memory_store=memory_store,
                ui_context={"mode": "api", "memory_status": memory_status},
            )
            if not ok:
                console.print(f"[{C['dim']}]Bye![/]")
                break
            continue

        runtime_system_prompt = build_runtime_system_prompt(
            BASE_SYSTEM_PROMPT,
            cfg=cfg,
            memory_store=memory_store,
            mode="api",
        )
        if messages and messages[0].get("role") == "system":
            messages[0]["content"] = runtime_system_prompt
        else:
            messages.insert(0, {"role": "system", "content": runtime_system_prompt})
        model_user_input = enrich_user_with_dream_recall(user_input, memory_store=memory_store)
        messages.append({"role": "user", "content": model_user_input})
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

    if memory_store is not None:
        try:
            memory_store.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
