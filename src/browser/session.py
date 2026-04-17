from pathlib import Path
from browser.controller import QwenBrowserController
from config.config import BROWSER_DATA_DIR, MEMORY_DIR
from ui.rich_ui import console
from ui.live_render import C
from ui.banner import print_banner_browser
from config.prompt import build_prompt_session, get_input_async, handle_slash

try:
    from memory.store import MemoryStore
    MEMORY_AVAILABLE = True
except ImportError:
    MEMORY_AVAILABLE = False
    MemoryStore = None




# ── browser session ───────────────────────────────────────────────────────────
async def browser_session(cfg: dict, headless: bool = False):
    controller = QwenBrowserController(
        headless=headless,
        data_dir=BROWSER_DATA_DIR,
    )
    await controller.start()

    # Initialize memory store
    memory_store = None
    if MEMORY_AVAILABLE and MemoryStore:
        try:
            db_url = cfg.get("memory_db_url", "")
            memory_store = MemoryStore(db_url=db_url if db_url else None)
            # Get or create session
            memory_store.get_or_create_session(
                cfg.get("session_id", "default"),
                model_main=cfg.get("model"),
                model_local=cfg.get("local_model")
            )
        except Exception as e:
            console.print(f"[{C['warn']}]Memory store init failed: {e}[/]")

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
                ok, _ = handle_slash(user_input, cfg, [], memory_store)
                if not ok:
                    break
                continue

            console.print()
            console.print(f"[{C['brand']}]◆ Qwen Coder (browser)[/] ", end="")
            await controller.send_prompt_and_get_response(user_input)

            # Store in memory if available
            if memory_store:
                try:
                    session_id = cfg.get("session_id", "default")
                    memory_store.add_message(session_id, "user", user_input, model="user")
                except Exception:
                    pass

            console.print()

    finally:
        await controller.close()
        if memory_store:
            memory_store.close()
        console.print(f"[{C['dim']}]Browser closed. Bye![/]")
