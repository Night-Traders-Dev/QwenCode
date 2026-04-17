"""
Fast local helper model integration.

This module adds a small local model lane for quick response gating and warmup
while the cloud/browser model is working. It probes the local MegaKernel
submodule for compatibility, but falls back to Ollama when the requested model
is not supported by the current upstream registry.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any, Dict, Optional

from memory.local_llm import LocalLLMClient


class FastLLMClient:
    """Small helper model for low-latency audit and warmup tasks."""

    def __init__(
        self,
        model: str = "qwen3.5:0.8b",
        backend: str = "auto",
        megakernel_model: str = "Qwen/Qwen3.5-0.8B",
        megakernel_path: str = "third_party/mirage",
        audit_threshold: float = 7.5,
    ):
        self.model = model
        self.requested_backend = (backend or "auto").strip().lower()
        if self.requested_backend not in {"auto", "ollama", "megakernel"}:
            self.requested_backend = "auto"
        self.megakernel_model = megakernel_model
        self.megakernel_path = Path(megakernel_path)
        self.audit_threshold = float(audit_threshold)
        self._ollama_client = LocalLLMClient(model=model)
        self._status: Optional[Dict[str, Any]] = None

    def _extract_registered_builders(self) -> list[str]:
        builder_file = self.megakernel_path / "python" / "mirage" / "mpk" / "models" / "qwen3" / "builder.py"
        if not builder_file.exists():
            return []

        text = builder_file.read_text(encoding="utf-8", errors="ignore")
        marker = "@register_model_builder("
        start = text.find(marker)
        if start < 0:
            return []

        start += len(marker)
        end = text.find(")", start)
        if end < 0:
            return []

        try:
            parsed = ast.literal_eval(f"[{text[start:end]}]")
        except Exception:
            return []
        return [item for item in parsed if isinstance(item, str)]

    def _probe_megakernel(self) -> Dict[str, Any]:
        registered = self._extract_registered_builders()
        status: Dict[str, Any] = {
            "present": self.megakernel_path.exists(),
            "registered_builders": registered,
            "supported": False,
            "reason": "",
        }

        if not status["present"]:
            status["reason"] = "MegaKernel submodule is not initialized."
            return status

        target_names = f"{self.megakernel_model} {self.model}".lower()
        if "qwen3.5" in target_names:
            status["reason"] = (
                "Current Mirage MPK wiring exposes Qwen3 builders only, so "
                "Qwen3.5 stays on the Ollama fast path."
            )
            return status

        status["reason"] = "MegaKernel runtime probing is wired, but this model path is not enabled yet."
        return status

    def get_status(self) -> Dict[str, Any]:
        """Return the resolved backend status for the fast helper model."""
        if self._status is not None:
            return dict(self._status)

        probe = self._probe_megakernel()
        status: Dict[str, Any] = {
            "model": self.model,
            "requested_backend": self.requested_backend,
            "resolved_backend": "unavailable",
            "available": False,
            "reason": probe.get("reason", ""),
            "megakernel_present": probe["present"],
            "megakernel_model": self.megakernel_model,
            "megakernel_registered_builders": probe["registered_builders"],
        }

        if self.requested_backend == "megakernel":
            status["resolved_backend"] = "megakernel"
            status["available"] = False
            self._status = status
            return dict(status)

        ollama_available = self._ollama_client.is_available()
        status["resolved_backend"] = "ollama"
        status["available"] = ollama_available
        if ollama_available:
            if self.requested_backend == "ollama":
                status["reason"] = ""
        else:
            status["reason"] = f"Ollama model {self.model} is not available."

        self._status = status
        return dict(status)

    def is_available(self) -> bool:
        return bool(self.get_status().get("available"))

    def warmup(self, force: bool = False) -> bool:
        """Warm the resolved backend if it is currently usable."""
        status = self.get_status()
        if not status["available"]:
            return False
        if status["resolved_backend"] == "ollama":
            return self._ollama_client.warmup(force=force)
        return False

    def quick_audit(self, response: str, prompt: str | None = None) -> Dict[str, Any]:
        """
        Run a fast audit to decide whether a stronger local audit is necessary.
        """
        status = self.get_status()
        if not status["available"]:
            return {
                "score": 5.0,
                "escalate": True,
                "summary": status.get("reason") or "Fast helper unavailable.",
                "issues": ["Fast helper unavailable."],
                "backend": status.get("resolved_backend"),
                "model": self.model,
            }

        context = f"Original prompt: {prompt}\n\n" if prompt else ""
        response_text = self._ollama_client.chat_complete(
            [
                {
                    "role": "system",
                    "content": (
                        "You are a fast response quality gate. Decide if the response should "
                        "be escalated to a larger audit model. Return JSON only with keys: "
                        "score, escalate, summary, issues, factual_risk. "
                        "Use a 1-10 score only. "
                        "Do not punish correct concise answers to simple prompts just for being brief. "
                        "Set escalate true only for incomplete, messy, contradictory, unsafe, or genuinely fact-heavy responses."
                    ),
                },
                {
                    "role": "user",
                    "content": f"{context}Response to audit:\n{response}",
                },
            ],
            temperature=0.0,
            max_tokens=220,
            think=False,
            response_format={"type": "json_object"},
        )

        parsed = self._parse_json_object(response_text)
        if not parsed:
            return {
                "score": 5.0,
                "escalate": True,
                "summary": "Could not parse fast audit response.",
                "issues": ["Could not parse fast audit response."],
                "backend": status.get("resolved_backend"),
                "model": self.model,
            }

        score = self._coerce_score(parsed.get("score", 5.0))
        issues = parsed.get("issues") or []
        if not isinstance(issues, list):
            issues = [str(issues)]
        factual_risk = bool(parsed.get("factual_risk", False) or self._heuristic_factual_risk(prompt, response))
        escalate = parsed.get("escalate")
        if escalate is None:
            escalate = bool(factual_risk or issues or score < self.audit_threshold)
        elif factual_risk:
            escalate = True

        return {
            "score": score,
            "escalate": bool(escalate),
            "summary": str(parsed.get("summary", "")).strip(),
            "issues": [str(issue).strip() for issue in issues if str(issue).strip()],
            "factual_risk": factual_risk,
            "backend": status.get("resolved_backend"),
            "model": self.model,
        }

    def should_escalate(self, audit_result: Dict[str, Any]) -> bool:
        """Return whether the quick audit recommends escalating to the larger local model."""
        if audit_result.get("escalate") is not None:
            return bool(audit_result["escalate"])
        score = self._coerce_score(audit_result.get("score", 5.0))
        issues = audit_result.get("issues") or []
        return bool(score < self.audit_threshold or issues)

    @staticmethod
    def _coerce_score(value: Any) -> float:
        try:
            score = float(value)
        except Exception:
            return 5.0
        if score > 10:
            score = score / 10.0
        if score < 1:
            return 1.0
        if score > 10:
            return 10.0
        return score

    @staticmethod
    def _parse_json_object(text: str) -> Dict[str, Any]:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start < 0 or end <= start:
            return {}
        try:
            parsed = json.loads(text[start:end])
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _heuristic_factual_risk(prompt: str | None, response: str) -> bool:
        prompt_text = (prompt or "").lower()
        response_text = (response or "").lower()
        prompt_markers = (
            "weather",
            "forecast",
            "temperature",
            "aqi",
            "news",
            "latest",
            "today",
            "tomorrow",
            "yesterday",
            "current",
            "price",
            "stock",
            "market",
            "exchange rate",
            "medical",
            "legal",
            "election",
            "ceo",
            "president",
        )
        if any(marker in prompt_text for marker in prompt_markers):
            return True
        return "http://" in response_text or "https://" in response_text


_fast_llm: Optional[FastLLMClient] = None
_fast_llm_key: Optional[tuple[Any, ...]] = None


def get_fast_llm(
    model: str = "qwen3.5:0.8b",
    backend: str = "auto",
    megakernel_model: str = "Qwen/Qwen3.5-0.8B",
    megakernel_path: str = "third_party/mirage",
    audit_threshold: float = 7.5,
) -> FastLLMClient:
    """Get or create the global fast helper LLM client instance."""
    global _fast_llm, _fast_llm_key
    key = (model, backend, megakernel_model, megakernel_path, float(audit_threshold))
    if _fast_llm is None or _fast_llm_key != key:
        _fast_llm = FastLLMClient(
            model=model,
            backend=backend,
            megakernel_model=megakernel_model,
            megakernel_path=megakernel_path,
            audit_threshold=audit_threshold,
        )
        _fast_llm_key = key
    return _fast_llm
