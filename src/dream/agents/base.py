"""
dream/agents/base.py — shared async model client for Dream agents.

Provides:
- Async OpenAI-compatible client setup
- Local/remote endpoint normalization
- Deterministic local-model calls for structured outputs
- Robust JSON extraction for array/object responses
"""

from __future__ import annotations

import inspect
import json
import os
from typing import Any

from openai import AsyncOpenAI

from dream.config import ModelConfig


class BaseAgent:
    """Shared async OpenAI-compatible wrapper used by Dream agents."""

    def __init__(self, config: ModelConfig) -> None:
        self.config = config
        self._client: AsyncOpenAI | None = None

    @property
    def system_prompt(self) -> str:
        raise NotImplementedError

    async def __aenter__(self) -> "BaseAgent":
        await self._ensure_client()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._client is None:
            return
        close = getattr(self._client, "close", None)
        if close:
            result = close()
            if inspect.isawaitable(result):
                await result
        self._client = None

    async def generate(
        self,
        prompt: str,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """Generate plain text from the configured model."""
        client = await self._ensure_client()
        request_kwargs: dict[str, Any] = {
            "model": self.config.name,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": prompt},
            ],
            "temperature": self.config.temperature if temperature is None else temperature,
            "max_tokens": self.config.max_tokens if max_tokens is None else max_tokens,
        }
        reasoning_effort = self._reasoning_effort()
        if reasoning_effort:
            request_kwargs["reasoning_effort"] = reasoning_effort

        response = await client.chat.completions.create(**request_kwargs)
        message = response.choices[0].message if response.choices else None
        content = (message.content or "").strip() if message else ""
        if content:
            return content

        reasoning = getattr(message, "reasoning", None) if message else None
        if reasoning:
            return str(reasoning).strip()
        raise RuntimeError(f"{self.__class__.__name__} returned empty content")

    async def generate_json(
        self,
        prompt: str,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> Any:
        """Generate and parse JSON from the configured model."""
        repair_prompt = (
            f"{prompt}\n\n"
            "Return valid JSON only. Do not include markdown fences, explanations, or commentary."
        )
        last_error: Exception | None = None
        for candidate in (prompt, repair_prompt):
            text = await self.generate(
                candidate,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            try:
                return self._parse_json(text)
            except Exception as exc:
                last_error = exc
        raise ValueError(f"Could not parse JSON response: {last_error}")

    async def _ensure_client(self) -> AsyncOpenAI:
        if self._client is None:
            base_url = self._normalize_base_url(self.config.base_url)
            api_key = self.config.api_key or self._default_api_key(base_url)
            self._client = AsyncOpenAI(
                base_url=base_url,
                api_key=api_key,
                timeout=self.config.timeout,
            )
        return self._client

    def _reasoning_effort(self) -> str | None:
        """Disable hidden thinking for local Ollama models to improve JSON compliance and speed."""
        base_url = self._normalize_base_url(self.config.base_url).lower()
        if "localhost:11434" in base_url or "127.0.0.1:11434" in base_url:
            return "none"
        return None

    @staticmethod
    def _default_api_key(base_url: str) -> str:
        lowered = base_url.lower()
        if "localhost:11434" in lowered or "127.0.0.1:11434" in lowered:
            return "ollama"
        return (
            os.environ.get("DASHSCOPE_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or "none"
        )

    @staticmethod
    def _normalize_base_url(base_url: str) -> str:
        base = (base_url or "").strip().rstrip("/")
        if not base:
            return "http://localhost:11434/v1"
        if base.endswith("/v1"):
            return base
        if "/compatible-mode/v1" in base:
            return base
        if "localhost:11434" in base or "127.0.0.1:11434" in base:
            return f"{base}/v1"
        return f"{base}/v1"

    @classmethod
    def _parse_json(cls, text: str) -> Any:
        payload = cls._extract_json_blob(text)
        return json.loads(payload)

    @staticmethod
    def coerce_string_list(data: Any, *preferred_keys: str) -> list[str]:
        """Accept either a bare JSON array or an object wrapper containing one."""
        if isinstance(data, list):
            return [str(item).strip() for item in data if str(item).strip()]
        if isinstance(data, dict):
            for key in preferred_keys:
                value = data.get(key)
                if isinstance(value, list):
                    return [str(item).strip() for item in value if str(item).strip()]
            for value in data.values():
                if isinstance(value, list):
                    return [str(item).strip() for item in value if str(item).strip()]
        return []

    @staticmethod
    def _extract_json_blob(text: str) -> str:
        raw = (text or "").strip()
        if not raw:
            raise ValueError("empty response")

        if raw.startswith("```"):
            lines = raw.splitlines()
            if len(lines) >= 3 and lines[-1].strip() == "```":
                raw = "\n".join(lines[1:-1]).strip()
                if raw.lower().startswith("json"):
                    raw = raw[4:].strip()

        for opener, closer in (("{", "}"), ("[", "]")):
            start = raw.find(opener)
            if start < 0:
                continue
            depth = 0
            in_string = False
            escape = False
            for idx in range(start, len(raw)):
                char = raw[idx]
                if escape:
                    escape = False
                    continue
                if char == "\\":
                    escape = True
                    continue
                if char == '"':
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if char == opener:
                    depth += 1
                elif char == closer:
                    depth -= 1
                    if depth == 0:
                        return raw[start:idx + 1]

        raise ValueError("no JSON object or array found")
