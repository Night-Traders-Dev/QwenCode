# QwenCode

QwenCode is a terminal-first Qwen harness with a browser runner, a local helper stack, PostgreSQL-backed memory, and a Dream training loop. It is built to feel like a polished operator console instead of a raw transcript dump.

## What it does

- Runs in **API mode** or **browser mode**
- Uses a **triple-model lane**
  - cloud/browser model for the main answer
  - `qwen3.5:4b` for heavier local formatting and audits
  - `qwen3.5:0.8b` for fast local gating and Dream verification
- Stores conversation, tool output, audits, and Dream knowledge in **PostgreSQL** or file fallback
- Ships a **Home UI** and section navigation for workspace, models, memory, tools, and Dream
- Renders answers more intelligently, including a **weather-style report view** for forecast-heavy responses
- Includes a **Dream live UI** for Gather → Verify → Examine → Adapt sessions

## Highlights

- **Home dashboard** on startup with `/home` and `/go <section>`
- **Professional terminal rendering** with responsive status panels and structured answer views
- **Semantic response rendering** for weather reports, Dream summaries, knowledge hits, and improved markdown fallback
- **Dream loop** with live progress UI, session summaries, and PostgreSQL sync
- **Default Dream research lane** that pulls fresh evidence from trusted internet sources and feeds it into Gather, Verify, Examine, and Adapt
- **Reinforcement-style curriculum memory** that tracks which concepts and source domains are helping or hurting progress
- **Dream source recall** that reuses prior trusted sources from PostgreSQL before falling back to the open web again
- **Dream-aware model context** so the cloud, browser, and local helper lanes know where Dream files, logs, schemas, and knowledge categories live
- **Dream cloud fallback** to the local 4B lane when the remote orchestrator is unavailable or misconfigured
- **Expanded toolset** for file reads, chunked file reads, shell, git, knowledge search, and Dream inspection
- **Structured tool-result views** so diagnostics and memory hits land as UI instead of raw log text
- **Warm local model path** while the cloud/browser model is working
- **MegaKernel / Mirage submodule** vendored for future fast-path work

## Installation

### Prerequisites

- Python 3.10+
- Chrome or Chromium
- Ollama for local models
- PostgreSQL if you want durable multi-session memory

### Setup

1. Clone the repo and fetch submodules:

```bash
git clone <repository-url>
cd QwenCode
git submodule update --init --recursive
```

2. Install dependencies:

```bash
uv sync
```

3. Pull the local models:

```bash
ollama pull qwen3.5:4b
ollama pull qwen3.5:0.8b
```

4. Configure your cloud key if you want API mode:

```bash
export DASHSCOPE_API_KEY="your-api-key"
# or
export OPENAI_API_KEY="your-api-key"
```

5. Configure PostgreSQL if you want database-backed memory:

```bash
export MEMORY_BACKEND=postgresql
export REQUIRE_POSTGRES=true
export MEMORY_DB_URL="postgresql://user:pass@localhost:5432/qwencode"
```

## Running QwenCode

### API mode

```bash
python src/qwencode.py
```

### Browser mode

```bash
python src/qwencode.py --browser
```

### Headless browser mode

```bash
python src/qwencode.py --browser --headless
```

## Home UI and navigation

On startup, QwenCode now opens on a home dashboard with section cards for:

- workspace
- models
- memory
- tools
- dream

Navigation commands:

- `/home`
- `/go workspace`
- `/go models`
- `/go memory`
- `/go tools`
- `/go dream`

## Slash commands

| Command | Description |
|---|---|
| `/help` | Show command help |
| `/clear` | Clear the current conversation |
| `/model [name]` | Show or change the active model |
| `/tools` | Open the tools section |
| `/config` | Show effective config |
| `/memory` | Show memory status |
| `/memory show` | Show recent messages |
| `/audit <text>` | Audit text with the local model |
| `/local <text>` | Send a prompt directly to the local model |
| `/queue` | Show task queue status |
| `/tokens` | Show token usage |
| `/home` | Open the home dashboard |
| `/go <section>` | Open a specific dashboard section |
| `/exit` | Quit |

## Smart response rendering

QwenCode no longer treats every answer as one generic markdown blob.

The runtime prompt context now also includes Dream system details for the main API model, browser model prompts, and the direct local helper prompt path. That context includes:

- where `src/run_dream.py` and `src/dream/` live
- where Dream memory JSON and `dream.log` normally live in the workspace
- what the Dream phases are
- how Dream memory JSON is structured
- which PostgreSQL categories store Dream knowledge
- which tools to use to inspect Dream artifacts and recall Dream knowledge

### Current rendering behavior

- standard markdown answers render with a cleaner lead-summary + body layout
- dense forecast-style answers can be recognized and rendered as a **weather report**
- Dream and memory-style summaries can be rendered as **diagnostic dashboards**
- knowledge search results can be rendered as **search tables**
- the status panel is multi-row and width-aware instead of a single overflow line
- browser-mode answers are shown as soon as the main response is ready, while the audit continues in the background

### Weather report rendering

If the final answer contains weather-heavy fields such as:

- current weather
- temperature
- feels like
- humidity
- wind
- UV index
- visibility
- forecast / weekend outlook

QwenCode will render:

- a current conditions summary
- metric cards
- today’s narrative
- advisories
- an extended forecast table when enough daily structure is present

This is handled in the UI renderer rather than by relying on the model to format perfectly.

### Structured diagnostic rendering

QwenCode also recognizes structured operational text such as:

- Dream memory summaries
- knowledge search results
- compact label/value diagnostics

Those are rendered into dashboard-style panels or tables so memory and tool output are easier to scan at a glance.

## Local model stack

QwenCode uses a three-lane local/cloud setup:

1. **Main cloud/browser lane**
   - cloud API model or Qwen web UI
2. **Local audit / formatter lane**
   - `qwen3.5:4b`
3. **Fast local gate**
   - `qwen3.5:0.8b`

### What the helper models do

- warm while the main lane is working
- gate easy answers quickly
- escalate to deeper local auditing only when needed
- support Dream verification and grading
- take over the Dream orchestrator role if the configured cloud key or endpoint fails

### MegaKernel / Mirage

The repo includes the `third_party/mirage` submodule for MegaKernel-related probing. The fast helper still uses the Ollama path for `qwen3.5:0.8b` because the current upstream branch does not yet expose a Qwen 3.5-compatible builder for the intended fast path.

## Tools

The built-in tool schemas currently expose:

| Tool | Purpose |
|---|---|
| `read_file` | Read a file |
| `read_file_chunk` | Read a line range with line numbers |
| `write_file` | Write a file |
| `run_bash` | Run a shell command |
| `git_status` | Show repo status |
| `git_diff` | Show a diff preview |
| `search_knowledge` | Search memory / knowledge storage |
| `inspect_dream_memory` | Inspect Dream memory JSON |
| `list_dream_assets` | Locate Dream files, logs, and latest Dream snapshot |
| `list_directory` | List files and folders |
| `search_files` | Search across files |
| `glob_files` | Find files by glob |

## Memory and PostgreSQL

QwenCode memory stores:

- conversation history
- tool executions
- audit results
- durable knowledge rows
- session metadata
- Dream summaries, cycle reports, and verified Dream knowledge

### Backends

1. **PostgreSQL**
   - recommended for long-running use
   - searchable knowledge rows
   - Dream sync works here too
2. **File fallback**
   - used when PostgreSQL is not configured and not required

### Dream data in PostgreSQL

Dream now writes:

- `dream_summary`
- `dream_cycle`
- `dream_knowledge`
- `dream_source`

Rows are keyed with the Dream session id so separate Dream runs on the same topic do not overwrite one another.
Stored `dream_source` rows can also be recalled in later Dream runs for the same topic, so PostgreSQL acts as both a memory log and a reusable research cache.

## Dream mode

Dream is the multi-agent training loop:

1. Gather
2. Verify
3. Examine
4. Adapt

By default, each Dream cycle also runs a reliable-source internet retrieval pass:

- uses Wikipedia plus trusted domains such as `.gov`, `.edu`, `.mil`, `.int`, standards bodies, docs sites, and research organizations
- uses a search engine only for discovery, then fetches and stores source content directly from trusted domains
- turns retrieved excerpts into candidate facts
- uses the retrieved evidence to ground question generation, verification, and gap analysis

Dream also keeps a lightweight reinforcement layer:

- concepts tied to repeated mistakes are pushed down in mastery
- concepts tied to improving scores are rewarded
- source domains that correlate with better cycles are rewarded
- future research queries are biased toward the lowest-mastery concepts first

### Run Dream

```bash
python src/run_dream.py "basic arithmetic"
```

If the configured cloud model cannot authenticate, Dream now probes the local 4B lane and automatically falls back to it for orchestration instead of crashing with a traceback.

### Run Dream without the live board

```bash
python src/run_dream.py "basic arithmetic" --plain
```

### Resume Dream from an existing memory file

```bash
python src/run_dream.py "basic arithmetic" --resume --memory dream_basic_arithmetic.json
```

### Run Dream without internet retrieval

```bash
python src/run_dream.py "basic arithmetic" --no-research
```

### Limit how many trusted sources Dream fetches per cycle

```bash
python src/run_dream.py "basic arithmetic" --research-sources 2
```

### Override Dream session id for PostgreSQL sync

```bash
python src/run_dream.py "basic arithmetic" --session-id dream-basic-v2
```

### Dream UI

The live Dream UI shows:

- topic and model stack
- cycle and phase
- knowledge size and best score
- subtopics
- weak areas
- recent activity
- phase-by-phase completion

### Dream performance notes

Recent Dream improvements include:

- batched medium-model test taking
- structured-response enforcement for the 4B and 0.8B lanes
- staged verification so the 0.8B spends less time generating explanations for obviously good statements
- PostgreSQL persistence for Dream summaries, knowledge, and fetched source evidence
- default reliable-source retrieval with cached refresh windows
- reinforcement-style concept and source scoring to steer later cycles

## Configuration

Configuration is stored in `~/.qwencode/config.json`. Important environment variables:

| Variable | Description | Default |
|---|---|---|
| `DASHSCOPE_API_KEY` | DashScope key | unset |
| `OPENAI_API_KEY` | alternate cloud key | unset |
| `QWEN_BASE_URL` | API base URL | DashScope compatible endpoint |
| `QWEN_MODEL` | default cloud model | `qwen3-coder-plus` |
| `LOCAL_ENABLED` | enable local helper model | `true` |
| `LOCAL_MODEL` | heavier local helper | `qwen3.5:4b` |
| `LOCAL_FAST_ENABLED` | enable fast helper lane | `true` |
| `LOCAL_FAST_MODEL` | fast helper model | `qwen3.5:0.8b` |
| `LOCAL_FAST_BACKEND` | `auto`, `ollama`, or `megakernel` | `auto` |
| `LOCAL_FORMAT_ENABLED` | enable local output reformatter | `false` |
| `AUDIT_ENABLED` | enable local auditing | `true` |
| `MEMORY_BACKEND` | `auto`, `postgresql`, or `file` | `auto` |
| `REQUIRE_POSTGRES` | fail instead of falling back | `false` |
| `MEMORY_DB_URL` | PostgreSQL connection URL | unset |
| `SESSION_ID` | main app session id | `default` |
| `MEGAKERNEL_MODEL` | MegaKernel model id | `Qwen/Qwen3.5-0.8B` |
| `MEGAKERNEL_PATH` | local Mirage checkout | `third_party/mirage` |

## Architecture

```text
src/
├── qwencode.py
├── run_dream.py
├── browser/
│   ├── controller.py
│   ├── session.py
│   └── transcript_mirror.py
├── config/
│   ├── config.py
│   └── prompt.py
├── dream/
│   ├── agents/
│   ├── memory/
│   ├── phases/
│   ├── config.py
│   └── session.py
├── memory/
│   ├── fast_llm.py
│   ├── local_llm.py
│   └── store.py
├── tools/
│   ├── api.py
│   ├── definitions.py
│   └── tools.py
└── ui/
    ├── banner.py
    ├── dream_ui.py
    ├── home.py
    ├── live_render.py
    ├── rich_ui.py
    └── task_tracker.py
```

## Performance tips

1. Keep `qwen3.5:4b` and `qwen3.5:0.8b` loaded locally.
2. Use browser headless mode after login is stable.
3. Prefer PostgreSQL once memory volume grows.
4. Let the fast helper lane clear easy answers before escalating to deeper audits.
5. Use the Dream live UI for long sessions; use `--plain` for log-heavy automation.

## Troubleshooting

### Browser mode

- If the profile is locked, QwenCode will attempt cleanup or a fallback browser profile.
- If OAuth is required, complete login in the browser window once and then retry headless mode.

### Local models

- If the local models are missing, run:

```bash
ollama pull qwen3.5:4b
ollama pull qwen3.5:0.8b
```

- If Ollama is not reachable:

```bash
ollama serve
```

### PostgreSQL

- If you require PostgreSQL, set both:

```bash
export MEMORY_BACKEND=postgresql
export REQUIRE_POSTGRES=true
```

- Verify `MEMORY_DB_URL` is valid and reachable from the same machine.

## License

MIT
