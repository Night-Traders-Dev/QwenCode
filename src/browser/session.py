from pathlib import Path
import hashlib
import json
import asyncio
import time
from browser.controller import QwenBrowserController
from config.config import BROWSER_DATA_DIR, MEMORY_DIR
from ui.rich_ui import console
from ui.live_render import C, render_response
from ui.banner import print_banner_browser
from ui.home import print_home_dashboard
from config.prompt import build_prompt_session, get_input_async, handle_slash
from dream.context import wrap_user_with_runtime_context

try:
    from memory.store import MemoryStore
    from memory.fast_llm import get_fast_llm
    from memory.local_llm import get_local_llm
    from ui.task_tracker import (
        get_task_queue, get_token_tracker, get_thinking_ui,
        Task, reset_trackers, run_task_with_timing, get_status_panel
    )
    MEMORY_AVAILABLE = True
except ImportError:
    MEMORY_AVAILABLE = False
    MemoryStore = None
    get_fast_llm = None
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
    fast_llm_client = None
    fast_llm_status = None
    local_llm_client = None
    memory_status = None
    if MEMORY_AVAILABLE and MemoryStore:
        try:
            db_url = cfg.get("memory_db_url", "")
            memory_store = MemoryStore(
                db_url=db_url if db_url else None,
                backend=cfg.get("memory_backend", "auto"),
                require_postgres=cfg.get("require_postgres", False),
            )
            memory_status = memory_store.get_status()
            # Initialize local LLM if enabled
            if cfg.get("local_enabled", True) and get_local_llm:
                local_llm_client = get_local_llm(cfg.get("local_model"))
                if not local_llm_client.is_available():
                    console.print(f"[{C['dim']}]Local LLM not available, auditing disabled[/]")
                    local_llm_client = None
            if cfg.get("local_fast_enabled", True) and get_fast_llm:
                fast_llm_client = get_fast_llm(
                    model=cfg.get("local_fast_model"),
                    backend=cfg.get("local_fast_backend", "auto"),
                    megakernel_model=cfg.get("megakernel_model"),
                    megakernel_path=cfg.get("megakernel_path", "third_party/mirage"),
                    audit_threshold=cfg.get("local_fast_audit_threshold", 7.5),
                )
                fast_llm_status = fast_llm_client.get_status()
                if not fast_llm_status.get("available"):
                    fast_llm_client = None

            local_models = []
            if local_llm_client:
                local_models.append(cfg.get("local_model"))
            if fast_llm_client and fast_llm_status:
                local_models.append(
                    f"fast:{cfg.get('local_fast_model')} ({fast_llm_status.get('resolved_backend')})"
                )
            memory_store.get_or_create_session(
                cfg.get("session_id", "default"),
                model_main=cfg.get("model"),
                model_local=", ".join(local_models) if local_models else None,
            )
        except Exception as e:
            console.print(f"[{C['warn']}]Memory store init failed: {e}[/]")

    try:
        await controller.ensure_logged_in()
        print_banner_browser(cfg)
        if memory_status:
            if memory_status["backend"] == "postgresql":
                console.print(f"[{C['ok']}]Memory backend:[/] PostgreSQL")
            else:
                reason = memory_status.get("fallback_reason") or "automatic fallback"
                console.print(f"[{C['warn']}]Memory backend:[/] file fallback ({reason})")
        if local_llm_client:
            console.print(f"[{C['ok']}]Local LLM:[/] {local_llm_client.model}")
        if fast_llm_client and fast_llm_status:
            backend = (fast_llm_status.get("resolved_backend") or "ollama").capitalize()
            console.print(f"[{C['ok']}]Fast Local LLM:[/] {fast_llm_client.model} via {backend}")
            if fast_llm_status.get("reason"):
                console.print(f"[{C['dim']}]Fast path note:[/] {fast_llm_status['reason']}")
        elif fast_llm_status and fast_llm_status.get("reason") and cfg.get("local_fast_backend", "auto") == "megakernel":
            console.print(f"[{C['warn']}]Fast path note:[/] {fast_llm_status['reason']}")
        print_home_dashboard(
            cfg,
            mode="browser",
            memory_store=memory_store,
            memory_status=memory_status,
        )
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
                ok, _ = handle_slash(
                    user_input,
                    cfg,
                    [],
                    memory_store,
                    ui_context={"mode": "browser", "memory_status": memory_status},
                )
                if not ok:
                    break
                continue

            # Create task for tracking
            task_id = f"task_{int(time.time() * 1000)}"
            session_id = cfg.get("session_id", "default")
            tool_history = []
            raw_response = ""
            audit_details = None
            assistant_tokens = 0
            response_rendered = False
            warmup_tasks = []
            use_local_formatter = bool(cfg.get("local_format_enabled", False))

            if local_llm_client:
                warmup_tasks.append(asyncio.create_task(asyncio.to_thread(local_llm_client.warmup)))
            if fast_llm_client:
                warmup_tasks.append(asyncio.create_task(asyncio.to_thread(fast_llm_client.warmup)))

            if memory_store:
                try:
                    memory_store.add_message(
                        session_id,
                        "user",
                        user_input,
                        model="user",
                        metadata={"task_id": task_id, "kind": "user_prompt"},
                    )
                except Exception as e:
                    console.print(f"[{C['warn']}]Could not persist user prompt: {e}[/]")

            if MEMORY_AVAILABLE and Task and cfg.get("audit_enabled", True) and (local_llm_client or fast_llm_client):
                # Run with full audit pipeline
                reset_trackers()
                task = Task(id=task_id, prompt=user_input)

                tracker = get_token_tracker()
                panel = get_status_panel()

                async def main_task():
                    nonlocal tool_history, raw_response, assistant_tokens, response_rendered
                    console.print(f"\n[{C['brand']}]◆ Qwen Coder (browser)[/] ", end="")
                    model_prompt = wrap_user_with_runtime_context(
                        user_input,
                        cfg=cfg,
                        memory_store=memory_store,
                        mode="browser",
                    )
                    result_text, tool_history = await controller.send_prompt_and_get_response(
                        model_prompt,
                        render_output=False,
                    )
                    raw_response = result_text
                    # Estimate tokens from response
                    if result_text:
                        estimated_tokens = max(1, len(result_text) // 4)
                        tracker.add_main(estimated_tokens)
                        assistant_tokens = tracker.main_tokens
                        panel.update(tokens_main=tracker.main_tokens)
                    return result_text

                async def audit_task(result):
                    nonlocal audit_details, assistant_tokens
                    if warmup_tasks:
                        await asyncio.gather(*warmup_tasks, return_exceptions=True)

                    formatted_result = result
                    local_tokens_used = 0

                    if use_local_formatter and local_llm_client and local_llm_client.is_available():
                        panel.update(stage="formatting", step="Formatting with local LLM")
                        formatted_result = await asyncio.to_thread(
                            local_llm_client.format_for_display,
                            result,
                            user_input,
                        )
                        if not (formatted_result or "").strip():
                            formatted_result = result
                        else:
                            local_tokens_used += max(1, len(formatted_result) // 4)

                    quick_audit = None
                    if fast_llm_client and fast_llm_client.is_available():
                        panel.update(stage="auditing", step=f"Fast audit with {fast_llm_client.model}")
                        quick_audit = await asyncio.to_thread(
                            fast_llm_client.quick_audit,
                            formatted_result,
                            user_input,
                        )
                        audit_details = quick_audit
                        local_tokens_used += max(1, len(json.dumps(quick_audit)) // 4)
                        if not fast_llm_client.should_escalate(quick_audit):
                            tracker.add_local(local_tokens_used)
                            assistant_tokens = tracker.total
                            panel.update(tokens_local=tracker.local_tokens)
                            task.result = formatted_result
                            return quick_audit

                    if local_llm_client and local_llm_client.is_available():
                        panel.update(stage="auditing", step="Auditing response quality")
                        audit_result = await asyncio.to_thread(
                            local_llm_client.audit_response,
                            formatted_result,
                            user_input,
                        )
                        if quick_audit:
                            audit_result["fast_gate"] = quick_audit
                        audit_details = audit_result
                        local_tokens_used += max(1, len(json.dumps(audit_result)) // 4)
                        tracker.add_local(local_tokens_used)
                        assistant_tokens = tracker.total
                        panel.update(tokens_local=tracker.local_tokens)
                        task.result = formatted_result
                        return audit_result

                    tracker.add_local(local_tokens_used)
                    assistant_tokens = tracker.total
                    panel.update(tokens_local=tracker.local_tokens)
                    task.result = formatted_result
                    return quick_audit or {"score": 5.0}

                async def on_main_complete(completed_task):
                    nonlocal response_rendered
                    if completed_task.result:
                        render_response(
                            completed_task.result,
                            title="Draft Answer" if use_local_formatter else "Answer",
                        )
                        response_rendered = True

                await run_task_with_timing(
                    task,
                    main_task,
                    audit_task,
                    enable_audit=True,
                    on_main_complete=on_main_complete,
                )

                if task.result and use_local_formatter and task.result != raw_response:
                    rendered = task.result if isinstance(task.result, str) else str(task.result)
                    render_response(rendered, title="Refined Answer")
                elif task.result and not response_rendered:
                    rendered = task.result if isinstance(task.result, str) else str(task.result)
                    render_response(rendered, title="Answer")

                if memory_store and task.result:
                    try:
                        assistant_model = cfg.get("local_model") if use_local_formatter else "qwen-coder (web)"
                        metadata = {
                            "task_id": task_id,
                            "main_model": "qwen-coder (web)",
                            "local_model": cfg.get("local_model"),
                            "fast_local_model": cfg.get("local_fast_model") if fast_llm_client else None,
                            "fast_local_backend": fast_llm_status.get("resolved_backend") if fast_llm_status else None,
                            "audit_score": task.audit_score,
                            "tool_calls": len(tool_history),
                            "raw_response_chars": len(raw_response),
                            "kind": "assistant_response",
                        }
                        memory_store.add_message(
                            session_id,
                            "assistant",
                            task.result,
                            model=assistant_model,
                            metadata=metadata,
                            tokens_used=assistant_tokens or tracker.total,
                        )
                        memory_store.upsert_knowledge(
                            key=f"response:{session_id}:{task_id}",
                            content=task.result,
                            source="assistant",
                            category="response",
                            session_id=session_id,
                            metadata=metadata,
                        )
                        if audit_details:
                            memory_store.set_memory("last_audit", audit_details, category="audit")
                            memory_store.set_memory(f"last_audit:{session_id}", audit_details, category="audit")
                            memory_store.upsert_knowledge(
                                key=f"audit:{session_id}:{task_id}",
                                content=json.dumps(audit_details, indent=2),
                                source=audit_details.get("model", cfg.get("local_model")),
                                category="audit",
                                session_id=session_id,
                                metadata={
                                    "task_id": task_id,
                                    "audit_score": task.audit_score,
                                    "kind": "audit_result",
                                },
                            )
                        for idx, (tool_name, args, result) in enumerate(tool_history):
                            success = not result.startswith("[error]")
                            memory_store.log_tool_execution(session_id, tool_name, args, result, success=success)
                            if success and result.strip():
                                arg_hash = hashlib.sha1(
                                    json.dumps(args, sort_keys=True).encode("utf-8")
                                ).hexdigest()[:12]
                                memory_store.upsert_knowledge(
                                    key=f"tool:{session_id}:{task_id}:{idx}:{tool_name}:{arg_hash}",
                                    content=result,
                                    source=tool_name,
                                    category="tool_result",
                                    session_id=session_id,
                                    metadata={
                                        "task_id": task_id,
                                        "arguments": args,
                                        "kind": "tool_result",
                                    },
                                )
                    except Exception as e:
                        console.print(f"[{C['warn']}]Could not persist assistant memory: {e}[/]")


            else:
                # Simple mode without audit
                console.print(f"\n[{C['brand']}]◆ Qwen Coder (browser)[/] ", end="")
                result_text, tool_history = await controller.send_prompt_and_get_response(user_input)
                if memory_store and result_text:
                    try:
                        assistant_tokens = max(1, len(result_text) // 4)
                        metadata = {
                            "task_id": task_id,
                            "main_model": "qwen-coder (web)",
                            "tool_calls": len(tool_history),
                            "kind": "assistant_response",
                        }
                        memory_store.add_message(
                            session_id,
                            "assistant",
                            result_text,
                            model="qwen-coder (web)",
                            metadata=metadata,
                            tokens_used=assistant_tokens,
                        )
                        memory_store.upsert_knowledge(
                            key=f"response:{session_id}:{task_id}",
                            content=result_text,
                            source="assistant",
                            category="response",
                            session_id=session_id,
                            metadata=metadata,
                        )
                        for idx, (tool_name, args, result) in enumerate(tool_history):
                            success = not result.startswith("[error]")
                            memory_store.log_tool_execution(session_id, tool_name, args, result, success=success)
                            if success and result.strip():
                                arg_hash = hashlib.sha1(
                                    json.dumps(args, sort_keys=True).encode("utf-8")
                                ).hexdigest()[:12]
                                memory_store.upsert_knowledge(
                                    key=f"tool:{session_id}:{task_id}:{idx}:{tool_name}:{arg_hash}",
                                    content=result,
                                    source=tool_name,
                                    category="tool_result",
                                    session_id=session_id,
                                    metadata={
                                        "task_id": task_id,
                                        "arguments": args,
                                        "kind": "tool_result",
                                    },
                                )
                    except Exception as e:
                        console.print(f"[{C['warn']}]Could not persist assistant memory: {e}[/]")

            if memory_store:
                try:
                    local_models = []
                    if local_llm_client:
                        local_models.append(cfg.get("local_model"))
                    if fast_llm_client and fast_llm_status:
                        local_models.append(
                            f"fast:{cfg.get('local_fast_model')} ({fast_llm_status.get('resolved_backend')})"
                        )
                    memory_store.get_or_create_session(
                        session_id,
                        model_main=cfg.get("model"),
                        model_local=", ".join(local_models) if local_models else None,
                    )
                except Exception:
                    pass

            console.print()

    finally:
        await controller.close()
        if memory_store:
            memory_store.close()
        console.print(f"[{C['dim']}]Browser closed. Bye![/]")
