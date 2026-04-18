import json
import re
import subprocess
from pathlib import Path
from typing import Optional
from config.config import MAX_OUTPUT_CHARS
from config.config import load_config
from ui.rich_ui import console
from ui.live_render import C, build_semantic_renderable
from rich import box
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text


# ── tool implementations ──────────────────────────────────────────────────────
def _truncate(s: str, n: int = MAX_OUTPUT_CHARS) -> str:
    if len(s) <= n:
        return s
    head = int(n * 0.6)
    tail = n - head
    return s[:head] + f"\n\n... [TRUNCATED {len(s) - n} chars] ...\n\n" + s[-tail:]

def tool_read_file(path: str) -> str:
    p = Path(path).expanduser()
    if not p.exists():
        return f"[error] File not found: {path}"
    if not p.is_file():
        return f"[error] Not a file: {path}"
    try:
        return _truncate(p.read_text(errors="replace"))
    except Exception as e:
        return f"[error] {e}"

def tool_read_file_chunk(path: str, start_line: int = 1, end_line: int = 200) -> str:
    p = Path(path).expanduser()
    if not p.exists():
        return f"[error] File not found: {path}"
    if not p.is_file():
        return f"[error] Not a file: {path}"
    start_line = max(1, int(start_line))
    end_line = max(start_line, int(end_line))
    try:
        lines = p.read_text(errors="replace").splitlines()
        chunk = lines[start_line - 1:end_line]
        if not chunk:
            return "(no lines in requested range)"
        numbered = [
            f"{line_no:>5} | {line}"
            for line_no, line in enumerate(chunk, start=start_line)
        ]
        return _truncate("\n".join(numbered))
    except Exception as e:
        return f"[error] {e}"

def tool_write_file(path: str, content: str) -> str:
    p = Path(path).expanduser()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        existed = p.exists()
        p.write_text(content)
        action = "Updated" if existed else "Created"
        return f"[ok] {action} {p} ({len(content)} bytes)"
    except Exception as e:
        return f"[error] {e}"

def tool_run_bash(
    command: str,
    timeout: int = 30,
    workdir: Optional[str] = None,
) -> str:
    cwd = Path(workdir).expanduser() if workdir else Path.cwd()
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True,
            timeout=timeout, cwd=cwd,
        )
        out = result.stdout + result.stderr
        return f"[exit {result.returncode}]\n" + _truncate(out.strip())
    except subprocess.TimeoutExpired:
        return f"[error] Command timed out after {timeout}s"
    except Exception as e:
        return f"[error] {e}"

def tool_list_directory(
    path: str,
    recursive: bool = False,
    pattern: Optional[str] = None,
) -> str:
    p = Path(path).expanduser()
    if not p.exists():
        return f"[error] Path not found: {path}"
    try:
        glob = p.rglob(pattern or "*") if recursive else p.glob(pattern or "*")
        entries = sorted(glob, key=lambda x: (x.is_file(), x.name))
        lines = [
            f"  {e.relative_to(p)}{'/' if e.is_dir() else ''}"
            for e in entries
        ]
        return "\n".join(lines) if lines else "(empty)"
    except Exception as e:
        return f"[error] {e}"

def tool_git_status(directory: str = ".") -> str:
    root = Path(directory).expanduser()
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "status", "--short", "--branch"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        out = (result.stdout + result.stderr).strip()
        return out or "(clean working tree)"
    except Exception as e:
        return f"[error] {e}"


def tool_git_diff(directory: str = ".", path: Optional[str] = None, target: str = "HEAD") -> str:
    root = Path(directory).expanduser()
    cmd = ["git", "-C", str(root), "diff", "--stat", "--patch", target]
    if path:
        cmd.extend(["--", path])
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=20,
        )
        out = (result.stdout + result.stderr).strip()
        return _truncate(out or "(no diff)")
    except Exception as e:
        return f"[error] {e}"


def tool_search_knowledge(
    query: str,
    limit: int = 10,
    category: Optional[str] = None,
    session_id: Optional[str] = None,
) -> str:
    try:
        from memory.store import MemoryStore
    except Exception as e:
        return f"[error] Memory store unavailable: {e}"

    cfg = load_config()
    store = None
    try:
        store = MemoryStore(
            db_url=cfg.get("memory_db_url") or None,
            backend=cfg.get("memory_backend", "auto"),
            require_postgres=cfg.get("require_postgres", False),
        )
        status = store.get_status()
        rows = store.search_knowledge(
            query=query,
            limit=limit,
            category=category,
            session_id=session_id,
        )
        header = f"Backend: {status.get('backend', 'unknown')}"
        if not rows:
            return f"{header}\n(no matches)"
        lines = [header, ""]
        for row in rows:
            lines.append(f"- {row.get('key', '(no key)')} [{row.get('category', 'general')}]")
            lines.append(f"  {row.get('content', '')[:300]}")
        return _truncate("\n".join(lines))
    except Exception as e:
        return f"[error] {e}"
    finally:
        if store is not None:
            store.close()


def tool_inspect_dream_memory(path: str = "dream_memory.json") -> str:
    dream_path = Path(path).expanduser()
    if not dream_path.exists():
        return f"(no Dream memory file at {dream_path})"
    try:
        data = json.loads(dream_path.read_text())
    except Exception as e:
        return f"[error] {e}"

    recent = data.get("cycle_history", [])[-5:]
    lines = [
        f"Topic: {data.get('topic', 'unknown')}",
        f"Subtopics: {', '.join(data.get('subtopics', [])[:6]) or '(none)'}",
        f"Knowledge statements: {len(data.get('knowledge_base', []))}",
        f"Flagged statements: {len(data.get('flagged_statements', []))}",
        f"Best score: {float(data.get('session_best_score', 0.0) or 0.0) * 100:.1f}%",
        f"Weak areas: {', '.join(data.get('weak_areas', [])[:5]) or '(none)'}",
        "",
        "Recent cycles:",
    ]
    if recent:
        for cycle in recent:
            lines.append(
                f"  cycle {cycle.get('cycle', '?')}: "
                f"score={float(cycle.get('score', 0.0)) * 100:.1f}% "
                f"passed={cycle.get('passed', False)} "
                f"added={cycle.get('n_statements_added', 0)}"
            )
    else:
        lines.append("  (none)")
    return _truncate("\n".join(lines))

def tool_search_files(
    pattern: str,
    directory: str = ".",
    glob: str = "*",
    max_results: int = 50,
) -> str:
    root = Path(directory).expanduser()
    try:
        rx = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        return f"[error] Invalid regex: {e}"
    hits = []
    try:
        for fpath in sorted(root.rglob(glob)):
            if not fpath.is_file():
                continue
            try:
                for i, line in enumerate(
                    fpath.read_text(errors="replace").splitlines(), 1
                ):
                    if rx.search(line):
                        hits.append(
                            f"{fpath.relative_to(root)}:{i}: {line.rstrip()}"
                        )
                        if len(hits) >= max_results:
                            return "\n".join(hits) + f"\n... (stopped at {max_results})"
            except Exception:
                pass
    except Exception as e:
        return f"[error] {e}"
    return "\n".join(hits) if hits else "(no matches)"

def tool_glob_files(
    pattern: str,
    directory: str = ".",
    max_results: int = 100,
) -> str:
    root = Path(directory).expanduser()
    try:
        results = sorted(root.glob(pattern))[:max_results]
        lines = [str(p.relative_to(root)) for p in results]
        suffix = f"\n... ({max_results} limit)" if len(results) == max_results else ""
        return "\n".join(lines) + suffix if lines else "(no matches)"
    except Exception as e:
        return f"[error] {e}"

TOOL_FNS = {
    "read_file":      tool_read_file,
    "read_file_chunk": tool_read_file_chunk,
    "write_file":     tool_write_file,
    "run_bash":       tool_run_bash,
    "git_status":     tool_git_status,
    "git_diff":       tool_git_diff,
    "search_knowledge": tool_search_knowledge,
    "inspect_dream_memory": tool_inspect_dream_memory,
    "list_directory": tool_list_directory,
    "search_files":   tool_search_files,
    "glob_files":     tool_glob_files,
}

def dispatch_tool(name: str, args: dict) -> str:
    fn = TOOL_FNS.get(name)
    if fn is None:
        return f"[error] Unknown tool: {name}"
    try:
        return fn(**args)
    except TypeError as e:
        return f"[error] Bad arguments for {name}: {e}"

# ── rendering ─────────────────────────────────────────────────────────────────
def print_tool_call(name: str, args: dict):
    icon = {
        "read_file": "📖",
        "read_file_chunk": "📄",
        "write_file": "✏️",
        "run_bash": "🔧",
        "git_status": "🌿",
        "git_diff": "🧾",
        "search_knowledge": "🧠",
        "inspect_dream_memory": "🛌",
        "list_directory": "📁",
        "search_files": "🔍",
        "glob_files": "🗂️",
    }.get(name, "🔩")
    args_str = " ".join(f"{k}={repr(v)}" for k, v in args.items())
    console.print(
        f"  [{C['tool']}]{icon} {name}[/] [{C['dim']}]{args_str[:120]}[/]"
    )

def print_tool_result(result: str, ok: bool = True):
    col   = C["ok"] if ok else C["err"]
    semantic = build_semantic_renderable(result, title="Tool Result")
    if semantic:
        console.print(semantic)
        return

    lines = result.strip().splitlines()
    preview = "\n".join(lines[:8])
    if len(lines) > 8:
        preview += f"\n... ({len(lines)} lines total)"
    console.print(
        Panel(
            Text(preview or "(empty)", style=C["text"]),
            title=f"[{col}]Tool Result[/]",
            border_style=col,
            box=box.ROUNDED,
            padding=(0, 1),
        )
    )

def render_assistant(text: str):
    fence_re = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)
    pos = 0
    for m in fence_re.finditer(text):
        before = text[pos : m.start()].strip()
        if before:
            console.print(Markdown(before))
        lang = m.group(1) or "text"
        code = m.group(2)
        console.print(
            Syntax(code, lang, theme="monokai",
                   line_numbers=len(code.splitlines()) > 10)
        )
        pos = m.end()
    tail = text[pos:].strip()
    if tail:
        console.print(Markdown(tail))
