import re
import time
import asyncio
from pathlib import Path
from ui.live_render import LiveRenderer
from tools.definitions import TOOLS
from tools.tools import dispatch_tool, print_tool_call, print_tool_result

# ── transcript mirror ─────────────────────────────────────────────────────────
class BrowserTranscriptMirror:
    PROBE_JS = r"""
    () => {
        const scope =
            document.querySelector('main') ||
            document.querySelector('[role="main"]') ||
            document.body;

        const normalize = (s) => (s || '')
            .replace(/\u00A0/g, ' ')
            .replace(/\r\n?/g, '\n')
            .replace(/[ \t]+/g, ' ')
            .replace(/ *\n */g, '\n')
            .replace(/\n{3,}/g, '\n\n')
            .trim();

        const badLine = /^(New Chat|Search Chats|Community|Coder|Projects|All chats|Today|Auto|AI-generated content may not be accurate.?|How can I help you today??)$/i;
        const skipLine = /^(Skip|Copy|Share|Regenerate|Sources?|Search(ing)? the web)$/i;

        const isVisible = (el) => {
            if (!el) return false;
            const st = window.getComputedStyle(el);
            if (!st) return false;
            if (st.display === 'none' || st.visibility === 'hidden') return false;
            const r = el.getBoundingClientRect();
            return r.width > 0 && r.height > 0;
        };

        const cleanText = (el) => {
            if (!el) return '';
            const clone = el.cloneNode(true);
            clone.querySelectorAll(
                'textarea,input,button,a[href],nav,aside,header,footer,script,style,svg,' +
                '[contenteditable="true"],[role="textbox"],' +
                '[class*="source"],[class*="cite"],[class*="reference"],' +
                '[class*="toolbar"],[class*="sidebar"],[class*="search"],' +
                '[class*="action"],[class*="feedback"]'
            ).forEach(n => n.remove());

            const raw = normalize(clone.innerText || clone.textContent || '');
            const cleaned = [];
            let lastBlank = false;

            for (const part of raw.split('\n')) {
                const line = part.trim();
                if (!line) {
                    if (!lastBlank && cleaned.length) {
                        cleaned.push('');
                    }
                    lastBlank = true;
                    continue;
                }
                if (badLine.test(line) || skipLine.test(line)) {
                    continue;
                }
                cleaned.push(line);
                lastBlank = false;
            }

            return normalize(cleaned.join('\n'));
        };

        const buttons = Array.from(document.querySelectorAll('button')).filter(isVisible);
        const busy =
            buttons.some(b => /stop|cancel/i.test((b.innerText || b.getAttribute('aria-label') || '').trim())) ||
            Array.from(document.querySelectorAll('[aria-busy="true"],[class*="load"],[class*="generat"],[class*="spin"]')).some(isVisible);

        const mainText = cleanText(scope);

        let thinkingText = '';
        let thinkingDone = false;
        let thinkingMethod = 'none';

        const thinkLabels = Array.from(scope.querySelectorAll('div,span,p,button,summary')).filter(isVisible).filter(el => {
            const t = normalize(el.innerText || el.textContent || '');
            return /^thinking(s+completed)?(s*[›>])?$/i.test(t);
        });

        const thinkCandidates = [];
        for (const label of thinkLabels) {
            const lt = normalize(label.innerText || label.textContent || '');
            if (/completed/i.test(lt)) thinkingDone = true;

            const wrappers = [
                label.closest('details'),
                label.closest('[class*="think"]'),
                label.parentElement,
                label.parentElement ? label.parentElement.parentElement : null,
                label.closest('section'),
                label.closest('article'),
                label.closest('div'),
            ].filter(Boolean);

            const seen = new Set();
            wrappers.forEach((w, idx) => {
                if (!w || seen.has(w)) return;
                seen.add(w);
                if (!isVisible(w)) return;
                const txt = cleanText(w);
                if (!txt) return;
                if (txt.length < lt.length) return;
                thinkCandidates.push({
                    text: txt,
                    score: txt.length + (idx * 25),
                    method: `thinking-wrapper-${idx}`,
                });
            });
        }

        if (thinkCandidates.length) {
            thinkCandidates.sort((a, b) => a.score - b.score);
            thinkingText = normalize(
                thinkCandidates[0].text
                    .replace(/^thinking(s+completed)?(s*[›>])?/i, '')
                    .replace(/\bSkip\b/gi, '')
            );
            thinkingMethod = thinkCandidates[0].method;
            if (/thinking completed/i.test(thinkCandidates[0].text)) {
                thinkingDone = true;
            }
        }

        const answerSelectors = [
            '[data-role="assistant"]',
            '[data-type="assistant"]',
            '.message-item.assistant',
            '.message--assistant',
            '.assistant-message',
            '.ai-message',
            '.markdown',
            '.prose',
            '[class*="markdown"]',
            '[class*="prose"]',
            'article',
            'section',
        ];

        const answerCandidates = [];
        const seenAnswer = new Set();
        let order = 0;

        const pushCandidate = (el, method) => {
            if (!el || !isVisible(el)) return;
            const txt = cleanText(el);
            if (!txt || txt.length < 40) return;
            if (seenAnswer.has(txt)) return;
            seenAnswer.add(txt);

            let score = txt.length;
            if (/thinking/i.test(txt)) score -= 140;
            if (/temperature|forecast|humidity|wind|condition|conditions|air quality|precipitation|uv index|```|•/i.test(txt)) score += 90;
            if (txt.split('\n').length >= 4) score += 30;
            if (/How can I help you today|AI-generated content may not be accurate|New Chat|Search Chats|Community|Projects|All chats/i.test(txt)) score -= 300;

            const r = el.getBoundingClientRect();
            if (r.top > 40) score += 10;
            if (r.left > 120) score += 10;
            score += order * 0.01;
            order += 1;

            answerCandidates.push({
                text: txt,
                score,
                method,
            });
        };

        for (const sel of answerSelectors) {
            scope.querySelectorAll(sel).forEach(el => pushCandidate(el, sel));
        }

        if (!answerCandidates.length) {
            scope.querySelectorAll('div,article,section').forEach(el => pushCandidate(el, 'generic'));
        }

        answerCandidates.sort((a, b) => b.score - a.score);
        let answerText = answerCandidates.length ? answerCandidates[0].text : '';
        let answerMethod = answerCandidates.length ? answerCandidates[0].method : 'none';

        if (answerText && /thinking completed/i.test(answerText)) {
            const parts = answerText.split(/thinking completed(s*[›>])?/i);
            answerText = normalize(parts[parts.length - 1]);
        }

        if (!answerText && /thinking completed/i.test(mainText)) {
            const parts = mainText.split(/thinking completed(s*[›>])?/i);
            answerText = normalize(parts[parts.length - 1]);
            answerMethod = 'main-split-thinking-completed';
        }

        return {
            main_text: mainText,
            thinking_text: thinkingText,
            thinking_done: thinkingDone,
            thinking_method: thinkingMethod,
            answer_text: answerText,
            answer_method: answerMethod,
            busy: !!busy,
        };
    }
    """

    def __init__(self, page: "Page", prompt: str):
        self.page = page
        self.prompt = (prompt or "").strip()
        self._baseline = {
            "main_text": "",
            "thinking_text": "",
            "thinking_done": False,
            "answer_text": "",
            "busy": False,
        }

    async def snapshot(self):
        self._baseline = self._extract_state(await self._probe())

    async def _probe(self) -> dict:
        try:
            return await self.page.evaluate(self.PROBE_JS) or {}
        except Exception:
            return {
                "main_text": "",
                "thinking_text": "",
                "thinking_done": False,
                "answer_text": "",
                "busy": False,
            }

    def _normalize_text(self, text: str) -> str:
        text = (text or "").replace(" ", " ")
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        lines = []
        last_blank = False
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                if lines and not last_blank:
                    lines.append("")
                last_blank = True
                continue
            if re.match(r"^(Skip|Copy|Share|Regenerate|Sources?|Search(ing)? the web|Auto)$", line, re.I):
                continue
            if re.match(r"^AI-generated content may not be accurate.?$", line, re.I):
                continue
            lines.append(line)
            last_blank = False
        out = "\n".join(lines).strip()
        out = re.sub(r"[ \t]+", " ", out)
        out = re.sub(r" *\n *", "\n", out)
        out = re.sub(r"\n{3,}", "\n\n", out)
        return out

    def _strip_prompt_echo(self, text: str) -> str:
        text = text.lstrip()
        if not self.prompt:
            return text
        if text.lower().startswith(self.prompt.lower()):
            text = text[len(self.prompt):].lstrip()
        return text

    def _extract_state(self, probe: dict) -> dict:
        main_text = self._normalize_text(probe.get("main_text", ""))
        thinking_text = self._normalize_text(probe.get("thinking_text", ""))
        answer_text = self._normalize_text(probe.get("answer_text", ""))

        if answer_text and "thinking completed" in answer_text.lower():
            answer_text = re.split(r"thinking completed(?:s*[›>])?", answer_text, flags=re.I)[-1].strip()

        answer_text = self._strip_prompt_echo(answer_text)

        if thinking_text and answer_text:
            if answer_text.startswith(thinking_text):
                answer_text = answer_text[len(thinking_text):].lstrip()
            if thinking_text.startswith(answer_text) and len(answer_text) > 40:
                thinking_text = thinking_text[len(answer_text):].strip()

        return {
            "main_text": main_text,
            "thinking_text": thinking_text,
            "thinking_done": bool(probe.get("thinking_done", False)),
            "answer_text": answer_text,
            "busy": bool(probe.get("busy", False)),
            "thinking_method": probe.get("thinking_method", "none"),
            "answer_method": probe.get("answer_method", "none"),
        }

    async def stream_response(
        self,
        renderer: LiveRenderer,
        timeout_ms: int = 120_000,
        poll_interval: float = 0.15,
        answer_stable_seconds: float = 3.5,
        post_thinking_grace_seconds: float = 4.0,
    ) -> str:
        renderer.reset()
        start = time.monotonic()
        last_change = start
        waiting_after_thinking = None
        started = False
        last = dict(self._baseline)

        while (time.monotonic() - start) * 1000 < timeout_ms:
            state = self._extract_state(await self._probe())
            now = time.monotonic()

            changed = any([
                state["main_text"] != last["main_text"],
                state["thinking_text"] != last["thinking_text"],
                state["thinking_done"] != last["thinking_done"],
                state["answer_text"] != last["answer_text"],
                state["busy"] != last["busy"],
            ])

            if (
                state["thinking_text"] != self._baseline["thinking_text"] or
                state["answer_text"] != self._baseline["answer_text"] or
                state["main_text"] != self._baseline["main_text"]
            ):
                started = True

            if started:
                renderer.update(
                    thinking_text=state["thinking_text"],
                    answer_text=state["answer_text"],
                    thinking_done=state["thinking_done"],
                )

                if changed:
                    last_change = now
                    last = dict(state)

                if state["answer_text"]:
                    waiting_after_thinking = None
                    if (now - last_change) >= answer_stable_seconds and not state["busy"]:
                        break
                else:
                    if state["thinking_text"] and state["thinking_done"] and not state["busy"]:
                        if waiting_after_thinking is None:
                            waiting_after_thinking = now
                        elif (now - waiting_after_thinking) >= post_thinking_grace_seconds:
                            break
                    else:
                        waiting_after_thinking = None

            await asyncio.sleep(poll_interval)

        renderer.finish()
        return renderer.answer_text or renderer.thinking_text


