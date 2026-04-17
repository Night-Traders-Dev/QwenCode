from pathlib import Path
import asyncio
import time
from browser.controller import QwenBrowserController
from config.config import BROWSER_DATA_DIR, MEMORY_DIR
from ui.rich_ui import console
from ui.live_render import C
from ui.banner import print_banner_browser
from config.prompt import build_prompt_session, get_input_async, handle_slash

try:
    from memory.store import MemoryStore
    from memory.local_llm import get_local_llm
    from ui.task_tracker import (
        get_task_queue, get_token_tracker, get_thinking_ui,
        Task, reset_trackers, run_task_with_timing, get_status_panel
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
    reset_trackers = None
    run_task_with_timing = None
    get_status_panel = None





# ── browser session ───────────────────────────────────────────────────────────
async def browser_session(cfg: dict, headless: bool = False):
    controller = QwenBrowserController(
        headless=headless,
        data_dir=BROWSER_DATA_DIR,
    )
    await controller.start()

    # Initialize memory store
    memory_store = None
    local_llm_client = None
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
            # Initialize local LLM if enabled
            if cfg.get("local_enabled", True) and get_local_llm:
                local_llm_client = get_local_llm(cfg.get("local_model"))
                if not local_llm_client.is_available():
                    console.print(f"[{C['dim']}]Local LLM not available, auditing disabled[/]")
                    local_llm_client = None
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

            # Create task for tracking
            task_id = f"task_{int(time.time() * 1000)}"

            if MEMORY_AVAILABLE and Task and local_llm_client and cfg.get("audit_enabled", True):
                # Run with full audit pipeline
                reset_trackers()
                task = Task(id=task_id, prompt=user_input)

                tracker = get_token_tracker()
                panel = get_status_panel()

                async def main_task():
                    console.print(f"\n[{C['brand']}]◆ Qwen Coder (browser)[/] ", end="")
                    result_text, _tool_history = await controller.send_prompt_and_get_response(user_input)
                    # Estimate tokens from response
                    if result_text:
                        estimated_tokens = max(1, len(result_text) // 4)
                        tracker.add_main(estimated_tokens)
                        panel.update(tokens_main=tracker.main_tokens)
                    return result_text

                async def audit_task(result):
                    if local_llm_client and local_llm_client.is_available():
                        # First format the result for professional display
                        panel.update(stage="formatting", step="Formatting with local LLM")
                        formatted_result = local_llm_client.format_for_display(result, user_input)

                        # Then audit the formatted result
                        panel.update(stage="auditing", step="Auditing response quality")
                        audit_result = local_llm_client.audit_response(formatted_result, user_input)

                        # Store in memory
                        if memory_store:
                            memory_store.set_memory(f"last_audit_{task_id}", audit_result)
                            memory_store.add_message(
                                cfg.get("session_id", "default"),
                                "assistant",
                                formatted_result,
                                model=cfg.get("local_model"),
                                tokens_used=tracker.local_tokens
                            )

                        # Update token count
                        tracker.add_local(len(formatted_result) // 4)
                        panel.update(tokens_local=tracker.local_tokens)

                        # Return formatted result as the actual response
                        task.result = formatted_result
                        return audit_result
                    return {"score": 5.0}

                await run_task_with_timing(task, main_task, audit_task, enable_audit=True)

                # Display the professionally formatted result
                if task.result:
                    from rich.markdown import Markdown
                    rendered = task.result if isinstance(task.result, str) else str(task.result)
                    console.print(Markdown(rendered))


            else:
                # Simple mode without audit
                console.print(f"\n[{C['brand']}]◆ Qwen Coder (browser)[/] ", end="")
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
