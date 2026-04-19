from pathlib import Path
import json
import os

# ── constants ─────────────────────────────────────────────────────────────────
CLAUDE_OPUS_MODEL="hf.co/ermiaazarkhalili/LFM2.5-1.2B-SFT-Claude-Opus-Reasoning-Unsloth-GGUF:Q8_0"

def get_model_display_name(model_id: str) -> str:
    """Return a shortened, user-friendly display name for a model identifier.

    Examples:
        - \"hf.co/ermiaazarkhalili/LFM2.5-1.2B-SFT-Claude-Opus-Reasoning-Unsloth-GGUF:Q8_0\"
          → \"LFM2.5-1.2B (Q8_0)\"
        - \"qwen3.5:4b\" → \"qwen3.5:4b\"
        - \"Qwen/Qwen3.5-0.8B\" → \"Qwen3.5-0.8B\"
    """
    if not model_id:
        return "Unknown"

    # Handle hf.co/ URLs
    if model_id.startswith("hf.co/"):
        parts = model_id.split("/")
        if len(parts) >= 3:
            # Extract model name and quantization
            model_name = parts[2]  # Everything after author/

            # Split off quantization suffix (e.g., :Q8_0)
            if ":" in model_name:
                name_part, quant = model_name.rsplit(":", 1)
                # Extract size pattern like 1.2B, 4B, 0.8B, etc.
                import re
                size_match = re.search(r'(\d+\.?\d*[BKMG])', name_part, re.IGNORECASE)
                size = size_match.group(1) if size_match else ""

                # Try to extract a meaningful short name
                # Look for patterns like LFM2.5-1.2B-SFT-Claude-Opus-Reasoning-Unsloth-GGUF
                segments = name_part.split("-")
                if len(segments) >= 2:
                    # Use first segment + size if available
                    short_name = segments[0]  # e.g., "LFM2.5"
                    if size and size not in short_name:
                        return f"{short_name}-{size} ({quant})"
                    elif size:
                        return f"{short_name} ({quant})"
                    else:
                        return f"{short_name} ({quant})"
                else:
                    return f"{name_part} ({quant})" if quant else name_part

            return model_name

        return model_id

    # Handle HuggingFace standard format (author/model)
    if "/" in model_id and not model_id.startswith("http"):
        return model_id.split("/")[-1]

    # Return as-is for simple identifiers
    return model_id


MISSING = []
VERSION          = "0.0.1"
CONFIG_DIR       = Path.home() / ".qwencode"
CONFIG_FILE      = CONFIG_DIR / "config.json"
HISTORY_FILE     = CONFIG_DIR / "history"
BROWSER_DATA_DIR = CONFIG_DIR / "browser_data"
MEMORY_DIR       = CONFIG_DIR / "memory"
DASHSCOPE_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL      = "qwen3-coder-plus"
LOCAL_BASE_URL     = "http://localhost:11434/v1"
LOCAL_API_KEY      = "ollama"
LOCAL_MODEL        = CLAUDE_OPUS_MODEL  #"qwen3.5:4b"
LOCAL_FAST_MODEL   = "Qwen/Qwen3.5-0.8B"
MEGAKERNEL_MODEL   = "Qwen/Qwen3.5-0.8B"
MEGAKERNEL_PATH    = "third_party/mirage"

MAX_TOOL_ITERS   = 20
MAX_OUTPUT_CHARS = 10000


# ── config ────────────────────────────────────────────────────────────────────
DEFAULT_CONFIG = {
    "base_url":    DASHSCOPE_BASE_URL,
    "api_key":     "",
    "model":       DEFAULT_MODEL,
    "temperature": 0.7,
    "max_tokens":  8192,
    "stream":      True,
    "local_model": LOCAL_MODEL,
    "local_enabled": True,
    "local_format_enabled": False,
    "local_fast_enabled": True,
    "local_fast_model": LOCAL_FAST_MODEL,
    "local_fast_backend": "auto",  # auto | ollama | megakernel
    "local_fast_audit_threshold": 7.5,
    "megakernel_model": MEGAKERNEL_MODEL,
    "megakernel_path": MEGAKERNEL_PATH,
    "audit_enabled": True,
    "terminal_shell_enabled": True,
    "memory_backend": "auto",  # auto | postgresql | file
    "require_postgres": False,
    "memory_db_url": "",  # PostgreSQL URL, empty uses file-based storage
    "session_id": "default",
}

def load_config() -> dict:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_FILE.exists():
        try:
            saved = json.loads(CONFIG_FILE.read_text())
            cfg.update(saved)
        except Exception:
            pass
    bool_keys = {
        "local_enabled",
        "local_format_enabled",
        "local_fast_enabled",
        "audit_enabled",
        "terminal_shell_enabled",
        "require_postgres",
    }
    int_keys = {"max_tokens"}
    float_keys = {"temperature", "local_fast_audit_threshold"}

    for env, key in [
        ("DASHSCOPE_API_KEY", "api_key"),
        ("OPENAI_API_KEY",    "api_key"),
        ("QWEN_BASE_URL",     "base_url"),
        ("QWEN_MODEL",        "model"),
        ("LOCAL_ENABLED",     "local_enabled"),
        ("LOCAL_MODEL",       "local_model"),
        ("LOCAL_FAST_ENABLED", "local_fast_enabled"),
        ("LOCAL_FAST_MODEL",   "local_fast_model"),
        ("LOCAL_FAST_BACKEND", "local_fast_backend"),
        ("LOCAL_FAST_AUDIT_THRESHOLD", "local_fast_audit_threshold"),
        ("MEGAKERNEL_MODEL",  "megakernel_model"),
        ("MEGAKERNEL_PATH",   "megakernel_path"),
        ("LOCAL_FORMAT_ENABLED", "local_format_enabled"),
        ("AUDIT_ENABLED",     "audit_enabled"),
        ("TERMINAL_SHELL_ENABLED", "terminal_shell_enabled"),
        ("MEMORY_BACKEND",    "memory_backend"),
        ("MEMORY_DB_URL",     "memory_db_url"),
        ("SESSION_ID",        "session_id"),
    ]:
        value = os.environ.get(env)
        if value is None or value == "":
            continue
        if key in bool_keys:
            cfg[key] = value.lower() in {"1", "true", "yes", "on"}
        elif key in int_keys:
            try:
                cfg[key] = int(value)
            except ValueError:
                pass
        elif key in float_keys:
            try:
                cfg[key] = float(value)
            except ValueError:
                pass
        else:
            cfg[key] = value
    require_postgres = os.environ.get("REQUIRE_POSTGRES")
    if require_postgres is not None:
        cfg["require_postgres"] = require_postgres.lower() in {"1", "true", "yes", "on"}
    return cfg

def save_config(cfg: dict):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    out = {k: v for k, v in cfg.items() if k != "api_key" or v}
    CONFIG_FILE.write_text(json.dumps(out, indent=2))