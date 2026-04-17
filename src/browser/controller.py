import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from playwright.async_api import async_playwright, BrowserContext, Page
from config.config import BROWSER_DATA_DIR
from ui.rich_ui import console
from ui.live_render import C, LiveRenderer
from tools.tools import dispatch_tool, print_tool_call, print_tool_result
from browser.transcript_mirror import BrowserTranscriptMirror


# ── browser controller ────────────────────────────────────────────────────────
class QwenBrowserController:
    SEL_TEXTAREA = "textarea"
    SEL_SEND_BTN = 'button[aria-label="Send"]'
    QWEN_CHAT_URL = "https://chat.qwen.ai/"

    RESPONSE_TIMEOUT_MS = 120_000
    LOGIN_TIMEOUT_MS = 120_000
    MAX_TOOL_ROUNDS = 20

    TOOL_CALL_CANDIDATES = [
        "[data-tool]",
        ".tool-call",
        "[data-testid*='tool']",
    ]

    def __init__(self, headless: bool = False, data_dir: Optional[Path] = None):
        self._headless = headless
        self._data_dir = str(data_dir or BROWSER_DATA_DIR)
        self._pw = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._renderer = LiveRenderer()

    async def start(self):
        self._pw = await async_playwright().start()
        base_dir = Path(self._data_dir)
        base_dir.mkdir(parents=True, exist_ok=True)

        launch_error = None

        # First try the normal persistent profile
        try:
            self._context = await self._launch_context(base_dir)
        except Exception as e:
            launch_error = e
            msg = str(e)

            if "ProcessSingleton" in msg or "SingletonLock" in msg or "profile is already in use" in msg:
                console.print(
                    f"[{C['warn']}]Profile is locked; attempting recovery...[/]"
                )

                if self._profile_seems_idle(base_dir):
                    self._cleanup_profile_locks(base_dir)
                    try:
                        self._context = await self._launch_context(base_dir)
                        launch_error = None
                    except Exception as e2:
                        launch_error = e2

                if launch_error is not None:
                    fallback_dir = self._make_fallback_profile_dir()
                    console.print(
                        f"[{C['warn']}]Using temporary browser profile:[/] {fallback_dir}"
                    )
                    self._context = await self._launch_context(fallback_dir)
            else:
                raise

        if self._context is None:
            raise launch_error or RuntimeError("Failed to launch browser context")

        pages = self._context.pages
        self._page = pages[0] if pages else await self._context.new_page()

    async def _launch_context(self, profile_dir: Path):
        return await self._pw.chromium.launch_persistent_context(
            str(profile_dir),
            headless=self._headless,
            channel="chrome",
            viewport={"width": 1280, "height": 900},
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
            ],
        )

    def _profile_seems_idle(self, profile_dir: Path) -> bool:
        try:
            result = subprocess.run(
                ["pgrep", "-af", str(profile_dir)],
                capture_output=True,
                text=True,
                timeout=3,
            )
            return result.returncode != 0
        except Exception:
            return True

    def _cleanup_profile_locks(self, profile_dir: Path):
        lock_names = [
            "SingletonLock",
            "SingletonCookie",
            "SingletonSocket",
        ]
        for name in lock_names:
            p = profile_dir / name
            if p.exists() or p.is_symlink():
                try:
                    p.unlink()
                    console.print(f"[{C['dim']}]Removed stale lock {p}[/]")
                except Exception as e:
                    console.print(f"[{C['warn']}]Could not remove {p}: {e}[/]")

    def _make_fallback_profile_dir(self) -> Path:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        fallback = Path(self._data_dir).parent / f"browser_data_run_{stamp}"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


    async def close(self):
        try:
            if self._context:
                await self._context.close()
        finally:
            if self._pw:
                await self._pw.stop()


    async def ensure_logged_in(self):
        page = self._page

        cookie_file = Path(self._data_dir) / "Default" / "cookies_to_inject.json"
        if cookie_file.exists():
            try:
                cookies = json.loads(cookie_file.read_text())
                await self._context.add_cookies(cookies)
                console.print(f"[{C['ok']}]Injected {len(cookies)} session cookies.[/]")
                cookie_file.unlink()
            except Exception as e:
                console.print(f"[{C['warn']}]Cookie injection failed: {e}[/]")

        await page.goto(self.QWEN_CHAT_URL, wait_until="domcontentloaded")
        try:
            await page.wait_for_selector(self.SEL_TEXTAREA, timeout=8_000)
            return
        except Exception:
            pass

        console.print(
            f"[{C['warn']}]⚠  Not logged in. Please complete OAuth in the browser window.[/]"
        )
        await page.wait_for_selector(self.SEL_TEXTAREA, timeout=self.LOGIN_TIMEOUT_MS)

    async def send_prompt_and_get_response(
        self, prompt: str
    ) -> tuple[str, list[tuple[str, dict, str]]]:
        page = self._page
        tool_history: list[tuple[str, dict, str]] = []

        mirror = BrowserTranscriptMirror(page, prompt)
        await mirror.snapshot()
        await self._submit(page, prompt)

        for _round_idx in range(self.MAX_TOOL_ROUNDS):
            final_text = await mirror.stream_response(
                self._renderer,
                timeout_ms=self.RESPONSE_TIMEOUT_MS,
            )
            console.print()

            pending = await self._collect_pending_tool_calls(page)
            if not pending:
                return final_text, tool_history

            console.print(f"[{C['accent']}]⚙  Tools (browser mode)[/]")
            result_parts = []
            for node, tool_name, args in pending:
                print_tool_call(tool_name, args)
                result = dispatch_tool(tool_name, args)
                print_tool_result(result, ok=not result.startswith("[error]"))
                tool_history.append((tool_name, args, result))
                result_parts.append(f"Tool `{tool_name}` result:```{result}```")
                try:
                    await node.evaluate("el => el.setAttribute('data-result-sent', '1')")
                except Exception:
                    pass

            tool_prompt = "".join(result_parts)
            console.print(f"[{C['brand']}◆ Qwen Coder (browser)[/] ", end="")
            mirror = BrowserTranscriptMirror(page, tool_prompt)
            await mirror.snapshot()
            await self._submit(page, tool_prompt)

        console.print(
            f"[{C['warn']}]⚠  Reached max tool rounds ({self.MAX_TOOL_ROUNDS}) in browser mode.[/]"
        )
        return self._renderer.answer_text or self._renderer.thinking_text or "" 
    async def _submit(self, page: "Page", text: str):
        textarea = await page.wait_for_selector(self.SEL_TEXTAREA, timeout=10_000)
        await textarea.fill(text)
        try:
            btn = await page.wait_for_selector(self.SEL_SEND_BTN, timeout=3_000)
            await btn.click()
        except Exception:
            await textarea.press("Enter")

    async def _collect_pending_tool_calls(
        self, page: "Page"
    ) -> list[tuple[Any, str, dict]]:
        pending = []
        for sel in self.TOOL_CALL_CANDIDATES:
            try:
                nodes = await page.query_selector_all(sel)
            except Exception:
                continue

            for node in nodes:
                try:
                    if await node.get_attribute("data-result-sent"):
                        continue
                    tool_name = await node.get_attribute("data-tool") or "unknown"
                    raw_args = (await node.text_content() or "").strip()
                    try:
                        args = json.loads(raw_args)
                    except json.JSONDecodeError:
                        args = {"raw": raw_args}
                    pending.append((node, tool_name, args))
                except Exception:
                    continue

            if pending:
                break

        return pending
