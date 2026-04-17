# QwenCode

A powerful AI coding harness that leverages Qwen Coder models for interactive development assistance, with browser-based interaction and local LLM support.

## Features

- **Dual-Model Architecture**: Use cloud-based Qwen Coder models alongside a local Ollama instance (qwen3.5:4b)
- **Browser Mode**: Interact with Qwen's web interface directly, including tool execution
- **Memory System**: Persistent conversation history using PostgreSQL or file-based storage
- **Local LLM Integration**:
  - Text formatting and cleanup
  - Prompt/response auditing for quality assurance
  - Auxiliary tasks while main model works
  - Summarization and key point extraction
- **Fish-Shell Style UX**: Command history suggestions and tab-completion for slash commands
- **Rich Terminal UI**: Beautiful output with colors, panels, and live rendering

## Installation

### Prerequisites

- Python 3.10+
- Node.js (for some dependencies)
- Chrome/Chromium browser
- Ollama (optional, for local LLM features)

### Setup

1. **Clone the repository**:
   ```bash
   git clone <repository-url>
   cd QwenCode
   ```

2. **Install dependencies**:
   ```bash
   pip install -e .
   # or using uv
   uv sync
   ```

3. **Configure API access** (for cloud models):
   ```bash
   export DASHSCOPE_API_KEY="your-api-key"
   # or
   export OPENAI_API_KEY="your-api-key"
   ```

4. **Set up Ollama** (optional, for local LLM):
   ```bash
   # Install Ollama from https://ollama.ai
   ollama pull qwen3.5:4b
   ```

5. **Set up PostgreSQL** (optional, for advanced memory):
   ```bash
   # Install PostgreSQL
   # Create database and set environment variable
   export MEMORY_DB_URL="postgresql://user:pass@localhost:5432/qwencode"
   ```

## Usage

### Basic Usage

```bash
# API mode (direct API calls)
python src/qwencode.py

# Browser mode (interact via browser)
python src/qwencode.py --browser

# Browser mode with headless Chrome
python src/qwencode.py --browser --headless
```

### Slash Commands

| Command | Description |
|---------|-------------|
| `/help` | Show help message |
| `/clear` | Clear conversation history |
| `/model [name]` | Show or change active model |
| `/tools` | List available tools |
| `/config` | Show configuration |
| `/memory` | Show memory status |
| `/memory show` | Show recent messages |
| `/audit <text>` | Audit text using local LLM |
| `/local <text>` | Send text to local LLM |
| `/exit` | Quit session |

### Keyboard Shortcuts

- `Ctrl-D`: Quit session
- `Ctrl-C`: Cancel current input
- `Alt-Enter`: New line in multiline input
- `Tab`: Auto-complete slash commands
- `↑/↓`: Navigate command history

## Configuration

Configuration is stored in `~/.qwencode/config.json`. You can also use environment variables:

| Variable | Description |
|----------|-------------|
| `DASHSCOPE_API_KEY` | API key for DashScope/Qwen |
| `OPENAI_API_KEY` | Alternative API key |
| `QWEN_BASE_URL` | Custom API base URL |
| `QWEN_MODEL` | Default model name |
| `LOCAL_MODEL` | Local Ollama model (default: qwen3.5:4b) |
| `MEMORY_DB_URL` | PostgreSQL connection URL |

## Memory System

The memory system provides persistent storage for:

- **Conversation History**: All messages are stored per session
- **Tool Executions**: Log of all tool calls and results
- **User Preferences**: Custom memories and settings
- **Session Metadata**: Model info, timestamps, etc.

### Storage Backends

1. **File-based** (default): JSON files in `~/.qwencode/memory/`
2. **PostgreSQL**: For multi-session, multi-user scenarios

## Local LLM Features

When Ollama is running with qwen3.5:4b, you can:

- **Audit Prompts**: Get feedback on prompt clarity and safety
  ```
  /audit Write a function to delete all files
  ```

- **Format Text**: Clean up raw output
  ```python
  from memory.local_llm import get_local_llm
  llm = get_local_llm()
  formatted = llm.format_text(raw_output, "markdown")
  ```

- **Summarize Content**: Get concise summaries
  ```python
  summary = llm.summarize(long_text, max_length=100)
  ```

- **Extract Key Points**: Get bullet-point summaries
  ```python
  points = llm.extract_key_points(document)
  ```

## Tools

Available tools for the AI to use:

| Tool | Description |
|------|-------------|
| `read_file` | Read file contents |
| `write_file` | Write/create files |
| `run_bash` | Execute shell commands |
| `list_directory` | List directory contents |
| `search_files` | Search for patterns in files |
| `glob_files` | Find files by glob pattern |

## Architecture

```
src/
├── qwencode.py          # Main entry point
├── browser/
│   ├── controller.py    # Browser automation
│   ├── session.py       # Session management
│   └── transcript_mirror.py  # DOM scraping
├── config/
│   ├── config.py        # Configuration handling
│   └── prompt.py        # Prompt session & commands
├── memory/
│   ├── store.py         # Memory storage backend
│   └── local_llm.py     # Local LLM client
├── tools/
│   ├── api.py           # Tool dispatch
│   ├── tools.py         # Tool implementations
│   └── definitions.py   # Tool schemas
└── ui/
    ├── banner.py        # Welcome banner
    ├── live_render.py   # Live output rendering
    └── rich_ui.py       # Rich console wrapper
```

## Troubleshooting

### Browser Mode Issues

- **Profile locked**: The system automatically handles profile locks, or uses a fallback profile
- **Login required**: Complete OAuth in the browser window when prompted
- **Headless failures**: Try without `--headless` for debugging

### Local LLM Issues

- **Model not found**: Run `ollama pull qwen3.5:4b`
- **Connection refused**: Ensure Ollama is running (`ollama serve`)
- **Slow responses**: Consider using a smaller model or increasing resources

### Memory Issues

- **PostgreSQL connection**: Verify URL format and credentials
- **File permissions**: Ensure `~/.qwencode/` is writable

## License

MIT License - see LICENSE file for details.

## Contributing

Contributions welcome! Please open an issue or submit a PR.
