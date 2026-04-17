import json
from typing import Optional, List
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.styles import Style as PTStyle
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import Completer, Completion
from rich.table import Table
from rich.panel import Panel
from rich.box import SIMPLE
from config.config import HISTORY_FILE, save_config, LOCAL_MODEL
from tools.definitions import TOOLS
from ui.rich_ui import console
from ui.live_render import C

try:
    from memory.store import MemoryStore
    from memory.local_llm import get_local_llm
    from ui.task_tracker import (
        get_task_queue, get_token_tracker, get_thinking_ui,
        Task, TaskQueue, reset_trackers
    )
    MEMORY_AVAILABLE = True
except ImportError:
    MEMORY_AVAILABLE = False
    MemoryStore = None
    get_local_llm = None
    get_task_queue = None
    get_token_tracker = None
    get_thinking_ui = None
    Task = None
    TaskQueue = None
    reset_trackers = None

# ── Custom completer for slash commands ───────────────────────────────────────
class SlashCompleter(Completer):
    SLASH_COMMANDS = [
        "/help", "/clear", "/model", "/tools", "/config", "/exit",
        "/memory", "/audit", "/local", "/queue", "/tokens"
    ]

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if text.startswith("/"):
            # Find the partial command
            partial = text.split()[0] if " " not in text else text
            for cmd in self.SLASH_COMMANDS:
                if cmd.startswith(partial):
                    yield Completion(cmd, start_position=-len(partial))

# ── prompt session ────────────────────────────────────────────────────────────
def print_help():
    t = Table(box=SIMPLE, show_header=False, padding=(0, 2))
    t.add_column(style=C["accent"])
    t.add_column(style=C["dim"])
    for k, v in [
        ("/help",              "Show this help"),
        ("/clear",             "Clear conversation history"),
        ("/model [name]",      "Show or change the active model"),
        ("/tools",             "List available tools"),
        ("/config",            "Show active configuration"),
        ("/memory",            "Show memory status and contents"),
        ("/audit [text]",      "Audit text using local LLM"),
        ("/local [text]",      "Send text to local LLM"),
        ("/queue",             "Show task queue status"),
        ("/tokens",            "Show token usage statistics"),
        ("/exit",              "Quit the session"),
        ("Ctrl-D",             "Quit"),
        ("Ctrl-C",             "Cancel current input"),
        ("",                   ""),
        ("Multiline input:",   "End with a blank line, or use Alt-Enter"),
    ]:
        t.add_row(k, v)
    console.print(Panel(t, title="Commands", border_style=C["dim"]))

def handle_slash(
    cmd: str, cfg: dict, messages: list, memory_store=None
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
        if memory_store and MEMORY_AVAILABLE:
            memory_store.clear_conversation(cfg.get("session_id", "default"))
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
        t = Table(box=SIMPLE, show_header=True,
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
    elif verb == "/memory":
        if not MEMORY_AVAILABLE:
            console.print(f"[{C['warn']}]Memory module not available[/]")
        elif memory_store is None:
            console.print(f"[{C['warn']}]Memory store not initialized[/]")
        else:
            session_id = cfg.get("session_id", "default")
            conv = memory_store.get_conversation(session_id, limit=10)
            memories = memory_store.get_all_memories()

            t = Table(box=SIMPLE, show_header=False)
            t.add_column(style=C["accent"])
            t.add_column(style=C["dim"])
            t.add_row("Session ID:", session_id)
            t.add_row("Messages stored:", str(len(conv)))
            t.add_row("Memories:", str(len(memories)))

            if arg == "show" and conv:
                console.print(Panel(t, title="Memory Status", border_style=C["dim"]))
                console.print("\n[bold]Recent messages:[/]")
                for msg in conv[-5:]:
                    role = msg.get('role', 'unknown')
                    content = msg.get('content', '')[:100]
                    console.print(f"  [{C['dim']}]{role}:[/] {content}...")
            else:
                console.print(Panel(t, title="Memory Status", border_style=C["dim"]))
                console.print(f"[{C['dim']}]Use /memory show to see recent messages[/]")
    elif verb == "/audit":
        if not MEMORY_AVAILABLE or not get_local_llm:
            console.print(f"[{C['warn']}]Local LLM not available[/]")
        else:
            local_llm = get_local_llm(cfg.get("local_model", LOCAL_MODEL))
            if not local_llm.is_available():
                console.print(f"[{C['warn']}]Local LLM ({local_llm.model}) not running. Start Ollama first.[/]")
            elif arg:
                audit_result = local_llm.audit_prompt(arg)
                console.print(f"\n[{C['brand']}]Prompt Audit Results:[/]")
                console.print(f"  Score: {audit_result.get('score', 'N/A')}/10")
                console.print(f"  Safe: {audit_result.get('safe', 'N/A')}")
                console.print(f"  Actionable: {audit_result.get('actionable', 'N/A')}")
                if audit_result.get('issues'):
                    console.print(f"  [{C['warn']}]Issues:[/]")
                    for issue in audit_result['issues']:
                        console.print(f"    - {issue}")
                if audit_result.get('suggestions'):
                    console.print(f"  [{C['ok']}]Suggestions:[/]")
                    for sug in audit_result['suggestions']:
                        console.print(f"    - {sug}")
            else:
                console.print(f"[{C['dim']}]Usage: /audit <text to audit>[/]")
    elif verb == "/local":
        if not MEMORY_AVAILABLE or not get_local_llm:
            console.print(f"[{C['warn']}]Local LLM not available[/]")
        else:
            local_llm = get_local_llm(cfg.get("local_model", LOCAL_MODEL))
            if not local_llm.is_available():
                console.print(f"[{C['warn']}]Local LLM ({local_llm.model}) not running. Start Ollama first.[/]")
            elif arg:
                console.print(f"\n[{C['brand']}]Local LLM ({local_llm.model}):[/]")
                try:
                    response = local_llm.chat_complete([
                        {"role": "user", "content": arg}
                    ])
                    console.print(response)
                except Exception as e:
                    console.print(f"[{C['error']}]Error: {e}[/]")
            else:
                console.print(f"[{C['dim']}]Usage: /local <text to send to local LLM>[/]")
    elif verb == "/queue":
        if not MEMORY_AVAILABLE or not get_task_queue:
            console.print(f"[{C['warn']}]Task queue not available[/]")
        else:
            queue = get_task_queue()
            t = Table(box=SIMPLE, show_header=False)
            t.add_column(style=C["accent"])
            t.add_column(style=C["dim"])
            t.add_row("Pending tasks:", str(queue.pending_count))
            if queue.current:
                t.add_row("Current task:", queue.current.id)
                t.add_row("Status:", queue.current.status.value)
                t.add_row("Duration:", queue.current.format_total_duration())
                if queue.current.audit_score is not None:
                    color = C["ok"] if queue.current.audit_score >= 7 else C["warn"] if queue.current.audit_score >= 5 else C["err"]
                    t.add_row("Audit score:", f"[{color}]{queue.current.audit_score:.1f}/10[/]")
            console.print(Panel(t, title="Task Queue", border_style=C["dim"]))
    elif verb == "/tokens":
        if not MEMORY_AVAILABLE or not get_token_tracker:
            console.print(f"[{C['warn']}]Token tracker not available[/]")
        else:
            tracker = get_token_tracker()
            t = Table(box=SIMPLE, show_header=False)
            t.add_column(style=C["accent"])
            t.add_column(style=C["dim"])
            t.add_row("Main LLM tokens:", f"{tracker.main_tokens:,}")
            t.add_row("Local LLM tokens:", f"{tracker.local_tokens:,}")
            t.add_row("Total tokens:", f"{tracker.total:,}")
            console.print(Panel(t, title="Token Usage", border_style=C["dim"]))
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
        "auto-suggestion": C["dim"],
        "completion-menu": f"bg:{C['panel']} {C['text']}",
        "completion-menu.current": f"bg:{C['accent']} {C['brand']}",
    })
    kb = KeyBindings()

    @kb.add("escape", "enter")
    def _newline(event):
        event.current_buffer.insert_text("\n")

    return PromptSession(
        history=hist,
        style=style,
        key_bindings=kb,
        multiline=False,
        auto_suggest=AutoSuggestFromHistory(),
        completer=SlashCompleter(),
        complete_while_typing=True,
    )

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