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