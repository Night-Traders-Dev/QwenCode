from typing import Optional
from config.config import MAX_OUTPUT_CHARS



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
    "write_file":     tool_write_file,
    "run_bash":       tool_run_bash,
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
        "read_file": "📖", "write_file": "✏️", "run_bash": "🔧",
        "list_directory": "📁", "search_files": "🔍", "glob_files": "🗂️",
    }.get(name, "🔩")
    args_str = " ".join(f"{k}={repr(v)}" for k, v in args.items())
    console.print(
        f"  [{C['tool']}]{icon} {name}[/] [{C['dim']}]{args_str[:120]}[/]"
    )

def print_tool_result(result: str, ok: bool = True):
    col   = C["ok"] if ok else C["err"]
    lines = result.strip().splitlines()
    preview = "\n".join(lines[:6])
    if len(lines) > 6:
        preview += f"\n  [{C['dim']}]... ({len(lines)} lines total)[/]"
    console.print(f"  [{col}]└─[/] [{C['dim']}]{preview}[/]")

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