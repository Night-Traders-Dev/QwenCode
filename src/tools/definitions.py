# ── tool definitions ──────────────────────────────────────────────────────────
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the full contents of a file on disk.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute or relative path to the file.",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file_chunk",
            "description": "Read a specific line range from a file with line numbers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file."},
                    "start_line": {"type": "integer", "description": "1-based start line."},
                    "end_line": {"type": "integer", "description": "1-based end line, inclusive."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write (or overwrite) a file on disk with the given content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path":    {"type": "string", "description": "Path to the file."},
                    "content": {"type": "string", "description": "Full content to write."},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_bash",
            "description": (
                "Run a bash shell command and return stdout + stderr. "
                "Avoid destructive commands; prefer read-only or reversible operations."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string",  "description": "The shell command to run."},
                    "timeout": {"type": "integer", "description": "Seconds before timeout (default 30)."},
                    "workdir": {"type": "string",  "description": "Working directory (default: cwd)."},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_status",
            "description": "Show concise git status for a repository.",
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {"type": "string", "description": "Repository directory (default: cwd)."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_diff",
            "description": "Show a git diff preview for the repo or a single path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {"type": "string", "description": "Repository directory (default: cwd)."},
                    "path": {"type": "string", "description": "Optional file path within the repo."},
                    "target": {"type": "string", "description": "Git target to diff against (default HEAD)."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_knowledge",
            "description": "Search the app's memory and knowledge database, including Dream categories like dream_summary, dream_cycle, dream_knowledge, and dream_source.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query."},
                    "limit": {"type": "integer", "description": "Maximum rows to return (default 10)."},
                    "category": {"type": "string", "description": "Optional knowledge category filter."},
                    "session_id": {"type": "string", "description": "Optional session filter."},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "inspect_dream_memory",
            "description": "Inspect a Dream memory JSON snapshot and recent learning progress. If no path is provided, the newest Dream memory file in the workspace is used.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the Dream memory JSON file."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dream_assets",
            "description": "Locate Dream files, logs, core code paths, and the latest Dream snapshot in the workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {"type": "string", "description": "Directory to scan for Dream artifacts."},
                    "limit": {"type": "integer", "description": "Maximum Dream memory or log files to list (default 8)."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "List files and directories at a given path (non-recursive by default).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path":      {"type": "string",  "description": "Directory to list."},
                    "recursive": {"type": "boolean", "description": "Recurse into subdirectories."},
                    "pattern":   {"type": "string",  "description": "Glob pattern filter, e.g. '*.py'."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "Search for a regex or literal pattern across files in a directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern":     {"type": "string",  "description": "Text or regex to search for."},
                    "directory":   {"type": "string",  "description": "Root directory to search in."},
                    "glob":        {"type": "string",  "description": "File glob to limit search, e.g. '*.py'."},
                    "max_results": {"type": "integer", "description": "Max matching lines (default 50)."},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "glob_files",
            "description": "Find files matching a glob pattern.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern":     {"type": "string",  "description": "Glob pattern, e.g. 'src/**/*.rs'."},
                    "directory":   {"type": "string",  "description": "Root directory (default: cwd)."},
                    "max_results": {"type": "integer", "description": "Max results (default 100)."},
                },
                "required": ["pattern"],
            },
        },
    },
]
