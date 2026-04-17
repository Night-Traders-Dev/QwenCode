# ── banners ───────────────────────────────────────────────────────────────────
from datetime import datetime
from pathlib import Path
from rich.panel import Panel
from rich import box
from ui.rich_ui import console
from ui.live_render import C, VERSION

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