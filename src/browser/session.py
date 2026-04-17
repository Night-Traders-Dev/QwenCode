import re
import asyncio
from pathlib import Path
from browser.controller import QwenBrowserController


CONFIG_DIR       = Path.home() / ".qwencode"

BROWSER_DATA_DIR = CONFIG_DIR / "browser_data"



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


