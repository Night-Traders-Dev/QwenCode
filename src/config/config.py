from pathlib import Path
import json
import os

# ── constants ─────────────────────────────────────────────────────────────────
MISSING = []
VERSION          = "0.5.0"
CONFIG_DIR       = Path.home() / ".qwencode"
CONFIG_FILE      = CONFIG_DIR / "config.json"
HISTORY_FILE     = CONFIG_DIR / "history"
BROWSER_DATA_DIR = CONFIG_DIR / "browser_data"
MEMORY_DIR       = CONFIG_DIR / "memory"
DASHSCOPE_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL      = "qwen3-coder-plus"
LOCAL_BASE_URL     = "http://localhost:11434/v1"
LOCAL_API_KEY      = "ollama"
LOCAL_MODEL        = "qwen3.5:4b"

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
    "audit_enabled": True,
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
    for env, key in [
        ("DASHSCOPE_API_KEY", "api_key"),
        ("OPENAI_API_KEY",    "api_key"),
        ("QWEN_BASE_URL",     "base_url"),
        ("QWEN_MODEL",        "model"),
        ("LOCAL_MODEL",       "local_model"),
        ("MEMORY_BACKEND",    "memory_backend"),
        ("MEMORY_DB_URL",     "memory_db_url"),
    ]:
        v = os.environ.get(env)
        if v:
            cfg[key] = v
    require_postgres = os.environ.get("REQUIRE_POSTGRES")
    if require_postgres is not None:
        cfg["require_postgres"] = require_postgres.lower() in {"1", "true", "yes", "on"}
    return cfg

def save_config(cfg: dict):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    out = {k: v for k, v in cfg.items() if k != "api_key" or v}
    CONFIG_FILE.write_text(json.dumps(out, indent=2))
