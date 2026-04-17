from typing import Optional
from config.config import HISTORY_FILE
from ui.rich_ui import console
from ui.live_render import C
from browser.session import build_prompt_session




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
