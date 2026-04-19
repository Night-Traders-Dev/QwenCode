from pathlib import Path
import hashlib
import json
import asyncio
import time
from browser.controller import QwenBrowserController
from config.config import BROWSER_DATA_DIR, HISTORY_FILE, MEMORY_DIR
from ui.rich_ui import console
from ui.live_render import C, render_response, format_response_text
from ui.banner import print_banner_browser
from ui.home import print_home_dashboard
from config.prompt import build_prompt_session, get_input_async, handle_slash
from dream.context import wrap_user_with_runtime_context, discover_dream_assets
from tools.definitions import TOOLS
from ui.terminal_shell import TerminalShell

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


def _clip(text: str, limit: int = 140) -> str:
    normalized = " ".join((text or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."


def _shell_section_text(section: str, cfg: dict, memory_store=None, memory_status: dict | None = None) -> str:
    section = (section or "home").strip().lower()
    assets = discover_dream_assets(Path.cwd())
    snapshot = assets["snapshot"]
    backend = (memory_status or {}).get("backend", "not initialized")
    local_model = cfg.get("local_model", "disabled") if cfg.get("local_enabled", True) else "disabled"
    fast_model = cfg.get("local_fast_model", "disabled") if cfg.get("local_fast_enabled", True) else "disabled"

    if section == "workspace":
        return "\n".join(
            [
                "Workspace",
                f"- cwd: {Path.cwd()}",
                f"- History: {HISTORY_FILE}",
                f"- Browser data: {BROWSER_DATA_DIR}",
            ]
        )

    if section == "models":
        return "\n".join(
            [
                "Models",
                f"- Cloud: {cfg.get('model', 'unknown')}",
                f"- Cloud endpoint: {cfg.get('base_url', 'unknown')}",
                f"- Local: {local_model}",
                f"- Fast local: {fast_model}",
                f"- Formatter: {'enabled' if cfg.get('local_format_enabled', False) else 'disabled'}",
            ]
        )

    if section == "memory":
        lines = [
            "Memory",
            f"- Backend: {backend}",
            f"- PostgreSQL enabled: {'yes' if (memory_status or {}).get('postgres_enabled') else 'no'}",
        ]
        if (memory_status or {}).get("fallback_reason"):
            lines.append(f"- Fallback: {memory_status['fallback_reason']}")
        if memory_store is not None:
            try:
                lines.append(f"- Knowledge rows: {memory_store.count_knowledge_entries():,}")
            except Exception:
                pass
            try:
                recent = memory_store.get_conversation(cfg.get("session_id", "default"), limit=5)
                lines.append(f"- Recent messages: {len(recent)}")
            except Exception:
                pass
        return "\n".join(lines)

    if section == "tools":
        lines = ["Tools", f"- Available tools: {len(TOOLS)}"]
        for tool in TOOLS:
            fn = tool["function"]
            lines.append(f"- {fn['name']}: {fn['description']}")
        return "\n".join(lines)

    if section == "dream":
        lines = [
            "Dream",
            f"- Topic: {snapshot['topic']}",
            f"- Cycles: {snapshot['cycles']}",
            f"- Knowledge statements: {snapshot['knowledge_statements']}",
            f"- Best score: {snapshot['best_score'] * 100:.1f}%",
            f"- Research sources: {snapshot['research_sources']}",
            f"- Latest memory file: {assets['latest_memory']}",
            f"- Latest log file: {assets['latest_log']}",
            f'- Run: uv run python src/run_dream.py "your topic"',
        ]
        if snapshot["weak_areas"]:
            lines.append("- Weak areas: " + ", ".join(snapshot["weak_areas"]))
        return "\n".join(lines)

    return "\n".join(
        [
            "QwenCode Home",
            f"- Workspace: {Path.cwd()}",
            f"- Cloud model: {cfg.get('model', 'unknown')}",
            f"- Local model: {local_model}",
            f"- Fast path: {fast_model}",
            f"- Memory backend: {backend}",
            f"- Dream topic: {snapshot['topic']}",
            "",
            "Quick navigation",
            "- /home",
            "- /go workspace",
            "- /go models",
            "- /go memory",
            "- /go tools",
            "- /go dream",
            "",
            "Commands",
            "- /help",
            "- /local <prompt>",
            "- /clear",
            "- /exit",
        ]
    )


def _shell_help_text() -> str:
    return "\n".join(
        [
            "Commands",
            "- /help",
            "- /home",
            "- /go workspace|models|memory|tools|dream",
            "- /model [name]",
            "- /config",
            "- /memory",
            "- /memory show",
            "- /tools",
            "- /dream",
            "- /queue",
            "- /tokens",
            "- /local <prompt>",
            "- /clear",
            "- /exit",
        ]
    )


async def _handle_shell_command(
    shell: TerminalShell,
    command: str,
    cfg: dict,
    memory_store=None,
    memory_status: dict | None = None,
    local_llm_client=None,
) -> bool:
    parts = command.strip().split(maxsplit=1)
    verb = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if verb == "/exit":
        shell.update_status(state="completed", stage="closing", detail="Closing browser session.", current_prompt="")
        shell.exit()
        return False

    if verb == "/clear":
        shell.clear_output()
        shell.update_status(state="idle", stage="ready", detail="Conversation view cleared.", current_prompt="")
        return True

    if verb == "/help":
        shell.append_entry("System", _shell_help_text())
        return True

    if verb == "/config":
        safe_cfg = {
            key: ("***" if key == "api_key" and value else value)
            for key, value in cfg.items()
        }
        shell.append_entry("System", json.dumps(safe_cfg, indent=2))
        return True

    if verb == "/model":
        if arg:
            cfg["model"] = arg
            shell.append_entry("System", f"Model preference updated to {arg}.")
        else:
            shell.append_entry("System", f"Current model preference: {cfg.get('model', 'unknown')}")
        return True

    if verb in {"/home", "/memory", "/tools", "/dream"}:
        if verb == "/memory" and arg == "show" and memory_store is not None:
            try:
                rows = memory_store.get_conversation(cfg.get("session_id", "default"), limit=5)
            except Exception:
                rows = []
            if not rows:
                shell.append_entry("System", "No recent memory rows available.")
            else:
                lines = ["Recent memory rows"]
                for row in rows[-5:]:
                    role = row.get("role", "unknown")
                    content = _clip(str(row.get("content", "")), 160)
                    lines.append(f"- {role}: {content}")
                shell.append_entry("System", "\n".join(lines))
            return True
        section = "home" if verb == "/home" else verb[1:]
        shell.append_entry("System", _shell_section_text(section, cfg, memory_store=memory_store, memory_status=memory_status))
        return True

    if verb == "/go":
        shell.append_entry("System", _shell_section_text(arg or "home", cfg, memory_store=memory_store, memory_status=memory_status))
        return True

    if verb == "/queue":
        shell.append_entry(
            "System",
            "\n".join(
                [
                    "Queue",
                    f"- Pending prompts: {shell.pending_count}",
                    f"- State: {shell.state}",
                    f"- Stage: {shell.stage}",
                ]
            ),
        )
        return True

    if verb == "/tokens":
        shell.append_entry(
            "System",
            "\n".join(
                [
                    "Token usage",
                    f"- Main: {shell.main_tokens:,}",
                    f"- Local: {shell.local_tokens:,}",
                ]
            ),
        )
        return True

    if verb == "/local":
        if not arg:
            shell.append_entry("System", "Usage: /local <prompt>")
            return True
        if local_llm_client is None or not local_llm_client.is_available():
            shell.append_entry("System", "Local LLM is not available.")
            return True
        shell.update_status(
            state="running",
            stage="local",
            detail=f"Local model {local_llm_client.model} is responding.",
            current_prompt=_clip(arg),
            reset_timer=True,
        )
        local_prompt = wrap_user_with_runtime_context(
            arg,
            cfg=cfg,
            memory_store=memory_store,
            mode="local",
        )
        response = await asyncio.to_thread(
            local_llm_client.chat_complete,
            [
                {
                    "role": "system",
                    "content": "You are the local QwenCode helper. Use the supplied QwenCode runtime context, including Dream system details, when it is relevant.",
                },
                {"role": "user", "content": local_prompt},
            ],
        )
        shell.append_entry("Local", format_response_text(response, title="Local Answer"))
        shell.update_status(
            state="completed",
            stage="local",
            detail=f"Local model {local_llm_client.model} completed.",
            current_prompt="",
        )
        return True

    shell.append_entry("System", f"Unknown command: {command}\nUse /help to see available commands.")
    return True


async def _browser_session_shell(
    controller: QwenBrowserController,
    cfg: dict,
    memory_store=None,
    memory_status: dict | None = None,
    local_llm_client=None,
    fast_llm_client=None,
    fast_llm_status: dict | None = None,
) -> None:
    model_bits = [f"Cloud {cfg.get('model', 'unknown')}"]
    if local_llm_client:
        model_bits.append(f"Local {local_llm_client.model}")
    if fast_llm_client:
        fast_backend = (fast_llm_status or {}).get("resolved_backend") or "auto"
        model_bits.append(f"Fast {fast_llm_client.model} via {fast_backend}")
    shell = TerminalShell(mode="browser", model_summary=" | ".join(model_bits))
    shell.append_entry("System", _shell_section_text("home", cfg, memory_store=memory_store, memory_status=memory_status))
    shell.update_status(state="idle", stage="ready", detail="Connected to Qwen web. Input stays available while tasks run.")

    session_id = cfg.get("session_id", "default")
    use_local_formatter = bool(cfg.get("local_format_enabled", False))

    async def process_prompt(user_input: str) -> None:
        task_id = f"task_{int(time.time() * 1000)}"
        tool_history = []
        raw_response = ""
        audit_details = None
        assistant_tokens = 0
        local_tokens_used = 0
        warmup_tasks = []

        if local_llm_client:
            warmup_tasks.append(asyncio.create_task(asyncio.to_thread(local_llm_client.warmup)))
        if fast_llm_client:
            warmup_tasks.append(asyncio.create_task(asyncio.to_thread(fast_llm_client.warmup)))

        shell.update_status(
            state="running",
            stage="preparing",
            detail="Shaping prompt and warming local models.",
            current_prompt=_clip(user_input),
            main_tokens=0,
            local_tokens=0,
            reset_timer=True,
        )

        if memory_store:
            try:
                memory_store.add_message(
                    session_id,
                    "user",
                    user_input,
                    model="user",
                    metadata={"task_id": task_id, "kind": "user_prompt"},
                )
            except Exception:
                pass

        model_prompt = wrap_user_with_runtime_context(
            user_input,
            cfg=cfg,
            memory_store=memory_store,
            mode="browser",
        )

        shell.update_status(state="running", stage="cloud", detail="Waiting on Qwen web.")
        result_text, tool_history = await controller.send_prompt_and_get_response(
            model_prompt,
            render_output=False,
        )
        raw_response = result_text or ""
        if raw_response:
            assistant_tokens = max(1, len(raw_response) // 4)
        shell.update_status(main_tokens=assistant_tokens, stage="review", detail="Reviewing and formatting the draft.")

        if warmup_tasks:
            await asyncio.gather(*warmup_tasks, return_exceptions=True)

        formatted_result = raw_response
        if use_local_formatter and raw_response and local_llm_client and local_llm_client.is_available():
            shell.update_status(state="running", stage="formatting", detail=f"Formatting with {local_llm_client.model}.")
            formatted = await asyncio.to_thread(
                local_llm_client.format_for_display,
                raw_response,
                user_input,
            )
            if (formatted or "").strip():
                formatted_result = formatted
                local_tokens_used += max(1, len(formatted_result) // 4)

        quick_audit = None
        if formatted_result and fast_llm_client and fast_llm_client.is_available():
            shell.update_status(state="auditing", stage="fast-audit", detail=f"Fast audit with {fast_llm_client.model}.")
            quick_audit = await asyncio.to_thread(
                fast_llm_client.quick_audit,
                formatted_result,
                user_input,
            )
            audit_details = quick_audit
            local_tokens_used += max(1, len(json.dumps(quick_audit)) // 4)

        if (
            formatted_result
            and local_llm_client
            and local_llm_client.is_available()
            and (
                quick_audit is None
                or getattr(fast_llm_client, "should_escalate", lambda _result: True)(quick_audit)
            )
        ):
            shell.update_status(state="auditing", stage="audit", detail=f"Quality audit with {local_llm_client.model}.")
            audit_result = await asyncio.to_thread(
                local_llm_client.audit_response,
                formatted_result,
                user_input,
            )
            if quick_audit:
                audit_result["fast_gate"] = quick_audit
            audit_details = audit_result
            local_tokens_used += max(1, len(json.dumps(audit_result)) // 4)

        final_result = (formatted_result or raw_response or "").strip()
        if not final_result:
            final_result = "No response was returned."

        shell.update_status(local_tokens=local_tokens_used)
        shell.append_entry("Assistant", format_response_text(final_result, title="Answer"))

        if memory_store:
            try:
                assistant_model = cfg.get("local_model") if use_local_formatter else "qwen-coder (web)"
                metadata = {
                    "task_id": task_id,
                    "main_model": "qwen-coder (web)",
                    "local_model": cfg.get("local_model"),
                    "fast_local_model": cfg.get("local_fast_model") if fast_llm_client else None,
                    "fast_local_backend": fast_llm_status.get("resolved_backend") if fast_llm_status else None,
                    "audit_score": (audit_details or {}).get("score"),
                    "tool_calls": len(tool_history),
                    "raw_response_chars": len(raw_response),
                    "kind": "assistant_response",
                }
                memory_store.add_message(
                    session_id,
                    "assistant",
                    final_result,
                    model=assistant_model,
                    metadata=metadata,
                    tokens_used=assistant_tokens + local_tokens_used,
                )
                memory_store.upsert_knowledge(
                    key=f"response:{session_id}:{task_id}",
                    content=final_result,
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
                            "audit_score": (audit_details or {}).get("score"),
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
            except Exception:
                pass

        score = None
        if isinstance(audit_details, dict):
            score = audit_details.get("score")
        detail = "Completed."
        if score is not None:
            detail = f"Completed. Audit score {score}/10."
        shell.update_status(
            state="completed",
            stage="done",
            detail=detail,
            current_prompt="",
            main_tokens=assistant_tokens,
            local_tokens=local_tokens_used,
        )

    async def worker() -> None:
        while True:
            prompt = await shell.next_input()
            if prompt.startswith("/"):
                keep_running = await _handle_shell_command(
                    shell,
                    prompt,
                    cfg,
                    memory_store=memory_store,
                    memory_status=memory_status,
                    local_llm_client=local_llm_client,
                )
                if not keep_running:
                    return
                if shell.state == "completed":
                    shell.update_status(state="idle", stage="ready", detail="Connected and ready.", current_prompt="")
                continue
            try:
                await process_prompt(prompt)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                shell.append_entry("System", f"Task failed: {exc}")
                shell.update_status(
                    state="failed",
                    stage="error",
                    detail=f"Task failed: {_clip(str(exc), 120)}",
                    current_prompt="",
                )

    worker_task = asyncio.create_task(worker())
    try:
        await shell.run_async()
    finally:
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass





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
        if cfg.get("terminal_shell_enabled", True):
            await _browser_session_shell(
                controller,
                cfg,
                memory_store=memory_store,
                memory_status=memory_status,
                local_llm_client=local_llm_client,
                fast_llm_client=fast_llm_client,
                fast_llm_status=fast_llm_status,
            )
            return

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
