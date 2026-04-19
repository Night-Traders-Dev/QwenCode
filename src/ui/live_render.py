import re
import shutil
import textwrap
from ui.rich_ui import console
from rich import box
from rich.columns import Columns
from rich.console import Group
from rich.markdown import Markdown
from rich.padding import Padding
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

# ── colour palette ────────────────────────────────────────────────────────────
C = {
    "brand":    "#5BA3F5",
    "accent":   "#A78BFA",
    "ok":       "#4ADE80",
    "warn":     "#FBBF24",
    "err":      "#F87171",
    "dim":      "#6B7280",
    "tool":     "#34D399",
    "code":     "#F59E0B",
    "thought":  "#9CA3AF",    # dim gray for thoughts
    "markdown": "#E5E7EB",   # light gray for markdown text
    "header":   "#5BA3F5",     # brand blue for headers
    "meta":     "#9CA3AF",
    "task":     "#34D399",
    "plan":     "#A78BFA",
    "debug":    "#FBBF24",
    "panel":    "#1b2330",
    "text":     "#e5e9f0",
}

VERSION = "0.0.1"
WEATHER_DAYS = ("Tonight", "Saturday", "Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday")


def normalize_markdown(text: str) -> str:
    """Clean extracted model output into readable markdown-ish prose."""
    text = (text or "").replace("\u00A0", " ")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"(?<!\n)(#{1,6}\s)", r"\n\1", text)
    text = re.sub(r"(?<!\n)([-*]\s)", r"\n\1", text)
    text = re.sub(r"(?<!\n)(\d+\.\s)", r"\n\1", text)
    text = re.sub(r"(?<=[A-Za-z0-9\)])(\[[^\]]+\]\([^)]+\))", r" \1", text)
    text = re.sub(r"(\[[^\]]+\]\([^)]+\))(?=[A-Za-z0-9])", r"\1 ", text)

    if "```" not in text and "\n\n" not in text and text.count("\n") < 2 and len(text) > 280:
        sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text)
        if 3 <= len(sentences) <= 24:
            text = "\n\n".join(
                " ".join(sentences[idx: idx + 2]).strip()
                for idx in range(0, len(sentences), 2)
                if " ".join(sentences[idx: idx + 2]).strip()
            )

    return text.strip()


def _extract_metric(pattern: str, text: str) -> str:
    match = re.search(pattern, text, re.IGNORECASE)
    return match.group(1).strip(" ,;") if match else ""


def _extract_line_value(label: str, text: str) -> str:
    match = re.search(rf"^{re.escape(label)}:\s*(.+)$", text, re.MULTILINE)
    return match.group(1).strip() if match else ""


def _clean_weather_value(value: str) -> str:
    cleaned = (value or "").strip()
    cleaned = re.sub(r"^[\s:;,-]+", "", cleaned)
    cleaned = re.sub(r"https?://\S+", "", cleaned)
    cleaned = re.sub(r"\bwww\.[^\s]+", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    replacements = {
        "Accu Weather": "AccuWeather",
        "Real Feel": "RealFeel",
    }
    for source, target in replacements.items():
        cleaned = cleaned.replace(source, target)
    return cleaned.strip(" ,;")


def _weather_text(text: str) -> str:
    prepared = (text or "").replace("\u00A0", " ")
    prepared = prepared.replace("\r\n", "\n").replace("\r", "\n")
    prepared = re.sub(r"(?<=[a-z0-9°%])(?=[A-Z])", " ", prepared)
    prepared = re.sub(r"(?<=[)])(?=[A-Z])", " ", prepared)
    prepared = re.sub(r"(?<=[A-Za-z])(?=\d{1,3}°)", " ", prepared)
    prepared = re.sub(r"(?<=[A-Za-z])(?=\d+%)", " ", prepared)
    prepared = re.sub(r"\s+", " ", prepared).strip()

    replacements = [
        (r"\bCurrent Weather in\b\s*", "\nCurrent Weather in "),
        (r"\bas of\b\s*", "\nas of: "),
        (r"\bCurrent Conditions\b(?!:)\s*", "\nCurrent Conditions: "),
        (r"\bCondition Details\b(?!:)\s*", "\nCondition Details: "),
        (r"\bToday's Forecast\b(?!:)\s*", "\nToday's Forecast: "),
        (r"\bWeekend Outlook\b(?!:)\s*", "\nWeekend Outlook: "),
        (r"\bFeels Like\b(?!:)\s*", "\nFeels Like: "),
        (r"\bUV Index\b(?!:)\s*", "\nUV Index: "),
        (r"\bVisibility\b(?!:)\s*", "\nVisibility: "),
        (r"\bHumidity\b(?!:)\s*", "\nHumidity: "),
        (r"\bTemperature\b(?!:)\s*", "\nTemperature: "),
        (r"\bConditions\b(?!:)\s*", "\nConditions: "),
        (r"\bWind\b(?!:)\s*", "\nWind: "),
        (r"\bSource\b(?!:)\s*", "\nSource: "),
        (r"Note\s*:\s*", "\nNote: "),
    ]
    for pattern, replacement in replacements:
        prepared = re.sub(pattern, replacement, prepared, flags=re.IGNORECASE)

    prepared = re.sub(r"\n{2,}", "\n", prepared)
    return prepared.strip()


def _parse_daily_forecast(text: str) -> list[dict[str, str]]:
    section_match = re.search(
        r"Weekend Outlook:\s*(.+?)(?=\bNote:|\bSource:|For the most up-to-the-minute alerts|$)",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if not section_match:
        return []

    section = section_match.group(1)
    day_alternation = "|".join(re.escape(day) for day in WEATHER_DAYS)
    day_pattern = re.compile(
        rf"\b({day_alternation})\b"
        r"\s*([0-9]{1,3}(?:°(?:\s*[FC])?)?)?\s*/\s*([0-9]{1,3}(?:°(?:\s*[FC])?)?)?"
        rf"\s*(.+?)(?=\b(?:{day_alternation})\b|$)",
        re.IGNORECASE | re.DOTALL,
    )
    rows = []
    for match in day_pattern.finditer(section):
        day, high, low, detail = match.groups()
        detail = re.sub(r"\s+", " ", detail).strip(" ,;")
        rows.append(
            {
                "day": day.title(),
                "high": high or "-",
                "low": low or "-",
                "detail": detail or "Forecast pending",
            }
        )
    return rows[:6]


def _parse_weather_report(text: str) -> dict | None:
    lowered = text.lower()
    keyword_hits = sum(
        1 for keyword in ("weather", "forecast", "temperature", "humidity", "wind", "feels like", "uv index")
        if keyword in lowered
    )
    if keyword_hits < 4:
        return None

    prepared = _weather_text(text)
    flat = re.sub(r"\s+", " ", prepared).strip()
    flat = re.sub(r"(?<=[A-Za-z])(?=\d+%)", " ", flat)

    location = _extract_metric(
        r"Current Weather in\s+(.+?)(?=\s+as of:|\s+Temperature:|\s+Conditions:|\s+Current Conditions:|$)",
        flat,
    )
    if not location:
        location = _extract_metric(r"Weather in\s+(.+?)(?=\s+as of:|\s+Temperature:|$)", flat)

    timestamp = _extract_metric(
        r"as of:\s+(.+?)(?=\s+(?:Condition Details:|Temperature:|Feels Like:|Conditions:|Current Conditions:|Humidity:|Wind:|UV Index:|Visibility:|Source:|Today's Forecast:|Weekend Outlook:|Note:|$))",
        flat,
    )
    conditions = _extract_metric(
        r"(?:Current Conditions:|Conditions:)\s+(.+?)(?=\s+(?:Temperature:|Feels Like:|Humidity:|Wind:|UV Index:|Visibility:|Source:|Today's Forecast:|Weekend Outlook:|Note:|$))",
        flat,
    )
    condition_detail = _extract_metric(
        r"Condition Details:\s+(.+?)(?=\s+(?:Temperature:|Feels Like:|Conditions:|Humidity:|Wind:|UV Index:|Visibility:|Source:|Today's Forecast:|Weekend Outlook:|Note:|$))",
        flat,
    )
    temperature = _extract_metric(r"Temperature:\s*([0-9\-]+(?:°)?(?:\s*[FC])?(?:\s*\([^)]+\))?)", flat)
    feels_like = _extract_metric(r"Feels Like:\s*([0-9\-]+(?:°)?(?:\s*[FC])?(?:\s*\([^)]+\))?)", flat)
    humidity = _extract_metric(r"Humidity:\s*([0-9\-]+%)", flat)
    wind = _extract_metric(r"Wind:\s*(.+?)(?=\s+(?:UV Index:|Visibility:|Source:|Today's Forecast:|Weekend Outlook:|Note:|$))", flat)
    uv_index = _extract_metric(r"UV Index:\s*(.+?)(?=\s+(?:Visibility:|Source:|Today's Forecast:|Weekend Outlook:|Note:|$))", flat)
    visibility = _extract_metric(r"Visibility:\s*(.+?)(?=\s+(?:Source:|Today's Forecast:|Weekend Outlook:|Note:|$))", flat)
    source = _extract_metric(r"Source:\s*(.+?)(?=\s+(?:Today's Forecast:|Weekend Outlook:|Note:|$))", flat)
    today = _extract_metric(
        r"Today's Forecast:\s*(.+?)(?=\s+(?:Weekend Outlook:|Note:|Source:|$))",
        flat,
    )
    note = _extract_metric(r"Note:\s*(.+)$", flat)
    daily = _parse_daily_forecast(prepared)

    if not any((location, temperature, conditions, today, daily)):
        return None

    return {
        "location": _clean_weather_value(re.sub(r"[^\w\s,./()%-]", "", location).strip(" ,")),
        "timestamp": _clean_weather_value(timestamp.rstrip(" :")),
        "conditions": _clean_weather_value(conditions or condition_detail),
        "temperature": _clean_weather_value(temperature),
        "feels_like": _clean_weather_value(feels_like),
        "humidity": _clean_weather_value(humidity),
        "wind": _clean_weather_value(wind),
        "uv_index": _clean_weather_value(uv_index),
        "visibility": _clean_weather_value(visibility),
        "source": _clean_weather_value(source),
        "today": _clean_weather_value(today),
        "note": _clean_weather_value(note),
        "daily": daily,
    }


def _render_weather_response(title: str, weather: dict) -> Group:
    current_summary = Group(
        Text(weather.get("location") or "Weather Report", style=f"bold {C['brand']}"),
        Text(weather.get("timestamp") or "Current conditions", style=C["dim"]),
        Text(weather.get("conditions") or "Conditions unavailable", style=C["text"]),
        Text(
            "  ".join(
                part for part in (
                    weather.get("temperature"),
                    f"Feels like {weather['feels_like']}" if weather.get("feels_like") else "",
                )
                if part
            ) or "Temperature unavailable",
            style=f"bold {C['text']}",
        ),
    )

    metric_cards = []
    for label, value, color in [
        ("Humidity", weather.get("humidity"), C["tool"]),
        ("Wind", weather.get("wind"), C["accent"]),
        ("UV Index", weather.get("uv_index"), C["warn"]),
        ("Visibility", weather.get("visibility"), C["ok"]),
    ]:
        if value:
            metric_cards.append(
                Panel(
                    Text.assemble((label + "\n", C["dim"]), (value, color)),
                    border_style=C["dim"],
                    box=box.ROUNDED,
                    padding=(0, 1),
                )
            )

    right_blocks = []
    if weather.get("today"):
        right_blocks.append(
            Panel(
                Text(weather["today"], style=C["text"]),
                title=f"[{C['accent']}]Today[/]",
                border_style=C["accent"],
                box=box.ROUNDED,
                padding=(0, 1),
            )
        )
    if weather.get("note"):
        right_blocks.append(
            Panel(
                Text(weather["note"], style=C["text"]),
                title=f"[{C['warn']}]Advisory[/]",
                border_style=C["warn"],
                box=box.ROUNDED,
                padding=(0, 1),
            )
        )

    blocks = [
        Rule(title=f"[{C['brand']}]{title}[/]", style=C["brand"]),
        Columns(
            [
                Panel(current_summary, border_style=C["brand"], box=box.ROUNDED, padding=(0, 1)),
                Group(*right_blocks) if right_blocks else Panel(Text("No forecast narrative available.", style=C["dim"]), border_style=C["dim"], box=box.ROUNDED),
            ],
            expand=True,
            equal=True,
        ),
    ]

    if metric_cards:
        blocks.append(Columns(metric_cards, expand=True, equal=True))

    if weather.get("daily"):
        table = Table(box=box.SIMPLE, show_header=True, header_style=C["brand"], expand=True)
        table.add_column("Day", style=C["accent"], no_wrap=True)
        table.add_column("High", style=C["warn"], justify="right", no_wrap=True)
        table.add_column("Low", style=C["ok"], justify="right", no_wrap=True)
        table.add_column("Outlook", style=C["text"], overflow="fold")
        for row in weather["daily"]:
            table.add_row(row["day"], row["high"], row["low"], row["detail"])
        blocks.append(
            Panel(
                table,
                title=f"[{C['tool']}]Extended Forecast[/]",
                border_style=C["tool"],
                box=box.ROUNDED,
                padding=(0, 1),
            )
        )

    if weather.get("source"):
        blocks.append(Text(f"Source: {weather['source']}", style=C["dim"]))

    return Group(*blocks)


def _parse_dream_report(text: str) -> dict | None:
    topic = _extract_line_value("Topic", text)
    knowledge = _extract_line_value("Knowledge statements", text)
    best_score = _extract_line_value("Best score", text)
    subtopics_raw = _extract_line_value("Subtopics", text)
    weak_raw = _extract_line_value("Weak areas", text)
    flagged = _extract_line_value("Flagged statements", text)
    if not topic or not (knowledge or best_score or "Recent cycles:" in text):
        return None

    cycle_pattern = re.compile(
        r"^\s*cycle\s+(\d+):\s*score=([0-9.]+%)\s+passed=(True|False)\s+added=(\d+)",
        re.MULTILINE,
    )
    cycles = [
        {
            "cycle": match.group(1),
            "score": match.group(2),
            "passed": match.group(3),
            "added": match.group(4),
        }
        for match in cycle_pattern.finditer(text)
    ]

    subtopics = [] if not subtopics_raw or subtopics_raw == "(none)" else [part.strip() for part in subtopics_raw.split(",") if part.strip()]
    weak_areas = [] if not weak_raw or weak_raw == "(none)" else [part.strip() for part in weak_raw.split(",") if part.strip()]

    return {
        "topic": topic,
        "knowledge": knowledge or "0",
        "flagged": flagged or "0",
        "best_score": best_score or "0.0%",
        "subtopics": subtopics,
        "weak_areas": weak_areas,
        "cycles": cycles,
    }


def _render_dream_report(title: str, report: dict) -> Group:
    blocks = [
        Rule(title=f"[{C['brand']}]{title}[/]", style=C["brand"]),
        Panel(
            Group(
                Text(report["topic"], style=f"bold {C['brand']}"),
                Text(
                    f"{len(report['subtopics'])} subtopics tracked"
                    if report["subtopics"]
                    else "No subtopics recorded yet",
                    style=C["dim"],
                ),
            ),
            border_style=C["brand"],
            box=box.ROUNDED,
            padding=(0, 1),
        ),
    ]

    metric_cards = []
    for label, value, color in [
        ("Knowledge", report.get("knowledge"), C["code"]),
        ("Flagged", report.get("flagged"), C["warn"]),
        ("Best Score", report.get("best_score"), C["ok"]),
        ("Cycles", str(len(report.get("cycles") or [])), C["accent"]),
    ]:
        metric_cards.append(
            Panel(
                Text.assemble((label + "\n", C["dim"]), (value, color)),
                border_style=C["dim"],
                box=box.ROUNDED,
                padding=(0, 1),
            )
        )
    blocks.append(Columns(metric_cards, expand=True, equal=True))

    detail_panels = []
    if report.get("subtopics"):
        detail_panels.append(
            Panel(
                Text("\n".join(f"- {item}" for item in report["subtopics"]), style=C["text"]),
                title=f"[{C['accent']}]Subtopics[/]",
                border_style=C["accent"],
                box=box.ROUNDED,
                padding=(0, 1),
            )
        )
    if report.get("weak_areas"):
        detail_panels.append(
            Panel(
                Text("\n".join(f"- {item}" for item in report["weak_areas"]), style=C["text"]),
                title=f"[{C['warn']}]Weak Areas[/]",
                border_style=C["warn"],
                box=box.ROUNDED,
                padding=(0, 1),
            )
        )
    elif detail_panels:
        detail_panels.append(
            Panel(
                Text("No weak areas recorded.", style=C["dim"]),
                title=f"[{C['ok']}]Weak Areas[/]",
                border_style=C["ok"],
                box=box.ROUNDED,
                padding=(0, 1),
            )
        )
    if detail_panels:
        blocks.append(Columns(detail_panels, expand=True, equal=True))

    if report.get("cycles"):
        table = Table(box=box.SIMPLE, show_header=True, header_style=C["brand"], expand=True)
        table.add_column("Cycle", style=C["accent"], justify="right", no_wrap=True)
        table.add_column("Score", style=C["ok"], justify="right", no_wrap=True)
        table.add_column("Passed", style=C["text"], no_wrap=True)
        table.add_column("Added", style=C["code"], justify="right", no_wrap=True)
        for cycle in report["cycles"]:
            passed_color = C["ok"] if cycle["passed"] == "True" else C["warn"]
            table.add_row(
                cycle["cycle"],
                cycle["score"],
                f"[{passed_color}]{cycle['passed']}[/]",
                cycle["added"],
            )
        blocks.append(
            Panel(
                table,
                title=f"[{C['tool']}]Recent Cycles[/]",
                border_style=C["tool"],
                box=box.ROUNDED,
                padding=(0, 1),
            )
        )

    return Group(*blocks)


def _parse_knowledge_search(text: str) -> dict | None:
    lines = text.splitlines()
    if not lines or not lines[0].strip().lower().startswith("backend:"):
        return None

    backend = lines[0].split(":", 1)[1].strip() if ":" in lines[0] else "unknown"
    rows = []
    current = None
    entry_re = re.compile(r"^-\s+(.+?)\s+\[([^\]]+)\]\s*$")
    for raw_line in lines[1:]:
        line = raw_line.rstrip()
        if not line.strip():
            continue
        match = entry_re.match(line.strip())
        if match:
            if current:
                rows.append(current)
            current = {
                "key": match.group(1).strip(),
                "category": match.group(2).strip(),
                "preview": "",
            }
            continue
        if current is not None:
            current["preview"] = (current["preview"] + " " + line.strip()).strip()

    if current:
        rows.append(current)
    if not rows:
        return None
    return {"backend": backend, "rows": rows}


def _render_knowledge_search(title: str, result: dict) -> Group:
    table = Table(box=box.SIMPLE, show_header=True, header_style=C["brand"], expand=True)
    table.add_column("Key", style=C["accent"], overflow="fold")
    table.add_column("Category", style=C["tool"], no_wrap=True)
    table.add_column("Preview", style=C["text"], overflow="fold")
    for row in result["rows"]:
        table.add_row(row["key"], row["category"], row["preview"] or "No preview available")

    return Group(
        Rule(title=f"[{C['brand']}]{title}[/]", style=C["brand"]),
        Text(f"Knowledge backend: {result['backend']}", style=C["dim"]),
        Panel(
            table,
            border_style=C["tool"],
            box=box.ROUNDED,
            padding=(0, 1),
        ),
    )


def _parse_fact_sheet(text: str) -> list[tuple[str, str]] | None:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not 3 <= len(lines) <= 10:
        return None

    pairs = []
    for line in lines:
        if line.startswith(("```", "- ", "* ")):
            return None
        match = re.match(r"^([A-Za-z][A-Za-z0-9 /()._-]{1,24}):\s+(.+)$", line)
        if not match:
            return None
        key, value = match.groups()
        pairs.append((key.strip(), value.strip()))

    return pairs if len(pairs) >= 3 else None


def _render_fact_sheet(title: str, pairs: list[tuple[str, str]]) -> Group:
    table = Table(box=box.SIMPLE, show_header=False, expand=True, padding=(0, 1))
    table.add_column(style=C["accent"], no_wrap=True)
    table.add_column(style=C["text"], overflow="fold")
    for key, value in pairs:
        table.add_row(key, value)

    return Group(
        Rule(title=f"[{C['brand']}]{title}[/]", style=C["brand"]),
        Panel(
            table,
            border_style=C["brand"],
            box=box.ROUNDED,
            padding=(0, 1),
        ),
    )


def build_semantic_renderable(text: str, title: str = "Response"):
    for parser, renderer in [
        (_parse_weather_report, _render_weather_response),
        (_parse_dream_report, _render_dream_report),
        (_parse_knowledge_search, _render_knowledge_search),
        (_parse_fact_sheet, _render_fact_sheet),
    ]:
        parsed = parser(text)
        if parsed:
            return renderer(title, parsed)
    return None


def _shell_width(default: int = 76) -> int:
    columns = shutil.get_terminal_size((100, 40)).columns
    return max(44, min(default, columns - 8))


def _box_lines(title: str, lines: list[str], width: int | None = None) -> str:
    inner_width = width or _shell_width()
    wrapped: list[str] = []
    for raw_line in lines:
        if raw_line == "":
            wrapped.append("")
            continue
        wrapped.extend(textwrap.wrap(raw_line, width=inner_width) or [""])

    border = "+" + "-" * (inner_width + 2) + "+"
    title_line = f"| {title[:inner_width].ljust(inner_width)} |"
    body = [f"| {line.ljust(inner_width)} |" for line in wrapped] or [f"| {' '.ljust(inner_width)} |"]
    return "\n".join([border, title_line, border, *body, border])


def _metric_box(title: str, rows: list[tuple[str, str]], width: int | None = None) -> str:
    valid_rows = [(label, value) for label, value in rows if value]
    if not valid_rows:
        return ""
    label_width = min(14, max(len(label) for label, _ in valid_rows))
    lines = [f"{label.ljust(label_width)} : {value}" for label, value in valid_rows]
    return _box_lines(title, lines, width=width)


def _code_block_text(language: str, code: str) -> str:
    code = (code or "").rstrip("\n")
    lines = code.splitlines() or [""]
    numbered = [f"{idx:>3} | {line}" for idx, line in enumerate(lines, start=1)]
    lang = (language or "text").strip() or "text"
    return f"```{lang}\n" + "\n".join(numbered) + "\n```"


def _format_markdown_paragraphs(text: str) -> str:
    width = _shell_width(default=88)
    paragraphs = [part.strip() for part in text.split("\n\n") if part.strip()]
    rendered: list[str] = []
    for paragraph in paragraphs:
        lines = paragraph.splitlines()
        if paragraph.startswith("#"):
            rendered.append(paragraph)
            continue
        if all(line.lstrip().startswith(("- ", "* ", "1.", "2.", "3.", "4.", "5.", "6.", "7.", "8.", "9.")) for line in lines):
            items = []
            for line in lines:
                prefix, content = (line[:2], line[2:].strip()) if line.lstrip().startswith(("- ", "* ")) else (line.split(".", 1)[0] + ".", line.split(".", 1)[1].strip())
                items.append(textwrap.fill(content, width=width, initial_indent=f"{prefix} ", subsequent_indent="   "))
            rendered.append("\n".join(items))
            continue
        rendered.append(textwrap.fill(" ".join(line.strip() for line in lines), width=width))
    return "\n\n".join(rendered)


def _format_code_sections(cleaned: str) -> str:
    parts = re.split(r"(```[\s\S]*?```)", cleaned)
    rendered: list[str] = []
    for part in parts:
        if not part.strip():
            continue
        if part.startswith("```"):
            match = re.match(r"```([^\n`]*)\n([\s\S]*?)```$", part.strip())
            if match:
                rendered.append(_code_block_text(match.group(1).strip(), match.group(2)))
            else:
                rendered.append(part.strip())
        else:
            rendered.append(_format_markdown_paragraphs(part.strip()))
    return "\n\n".join(item for item in rendered if item.strip())


def _looks_like_code(text: str) -> bool:
    lines = [line for line in (text or "").splitlines() if line.strip()]
    if not lines:
        return False
    score = 0
    for line in lines[:20]:
        stripped = line.strip()
        if stripped.startswith(("Traceback", "File ", "def ", "class ", "import ", "from ", "$ ", "> ")):
            score += 2
        if any(token in stripped for token in ("{", "}", "=>", "();", "::", "</", "<div", "```")):
            score += 1
    return score >= 4


def _format_weather_text(title: str, weather: dict) -> str:
    lines = [f"{title}: {weather.get('location') or 'Weather Report'}"]
    if weather.get("timestamp"):
        lines.append(f"As of {weather['timestamp']}")
    summary = "\n".join(lines).strip()
    metrics = _metric_box(
        "Current Conditions",
        [
        ("Conditions", weather.get("conditions")),
        ("Temperature", weather.get("temperature")),
        ("Feels like", weather.get("feels_like")),
        ("Humidity", weather.get("humidity")),
        ("Wind", weather.get("wind")),
        ("UV index", weather.get("uv_index")),
        ("Visibility", weather.get("visibility")),
        ],
    )
    blocks = [summary]
    if metrics:
        blocks.append(metrics)
    if weather.get("today"):
        blocks.append(_box_lines("Today", [weather["today"]]))
    if weather.get("daily"):
        blocks.append(
            _box_lines(
                "Extended Forecast",
                [
                    f"{row['day']:<10} High {row['high']:<6} Low {row['low']:<6} {row['detail']}"
                    for row in weather["daily"]
                ],
            )
        )
    if weather.get("note"):
        blocks.append(_box_lines("Advisory", [weather["note"]]))
    if weather.get("source"):
        blocks.append(_box_lines("Source", [weather["source"]]))
    return "\n\n".join(block for block in blocks if block.strip())


def _format_dream_text(title: str, report: dict) -> str:
    lines = [
        f"{title}: {report.get('topic') or 'Dream Summary'}",
        f"Knowledge statements: {report.get('knowledge', '0')}",
        f"Flagged statements: {report.get('flagged', '0')}",
        f"Best score: {report.get('best_score', '0.0%')}",
        f"Cycles: {len(report.get('cycles') or [])}",
    ]
    if report.get("subtopics"):
        lines.extend(["", "Subtopics"] + [f"- {item}" for item in report["subtopics"]])
    if report.get("weak_areas"):
        lines.extend(["", "Weak Areas"] + [f"- {item}" for item in report["weak_areas"]])
    if report.get("cycles"):
        lines.extend(["", "Recent Cycles"])
        for cycle in report["cycles"]:
            lines.append(
                f"- Cycle {cycle['cycle']}: score {cycle['score']}, passed {cycle['passed']}, added {cycle['added']}"
            )
    return "\n".join(lines).strip()


def _format_knowledge_text(title: str, result: dict) -> str:
    lines = [f"{title}: knowledge backend {result['backend']}", ""]
    for row in result["rows"]:
        lines.append(f"- {row['key']} [{row['category']}]")
        if row.get("preview"):
            lines.append(f"  {row['preview']}")
    return "\n".join(lines).strip()


def _format_fact_sheet_text(title: str, pairs: list[tuple[str, str]]) -> str:
    return _metric_box(title, pairs) or title


def format_response_text(text: str, title: str = "Response") -> str:
    cleaned = normalize_markdown(text)
    if not cleaned:
        return ""

    if "```" in (text or ""):
        return _format_code_sections((text or "").replace("\r\n", "\n").replace("\r", "\n"))

    weather = _parse_weather_report(text)
    if weather:
        return _format_weather_text(title, weather)

    dream = _parse_dream_report(text)
    if dream:
        return _format_dream_text(title, dream)

    knowledge = _parse_knowledge_search(text)
    if knowledge:
        return _format_knowledge_text(title, knowledge)

    fact_sheet = _parse_fact_sheet(text)
    if fact_sheet:
        return _format_fact_sheet_text(title, fact_sheet)

    if _looks_like_code(cleaned):
        return _code_block_text("text", cleaned)

    return _format_markdown_paragraphs(cleaned)


def render_response(text: str, title: str = "Response"):
    """Render assistant output in a readable, width-aware layout."""
    cleaned = normalize_markdown(text)
    if not cleaned:
        return

    semantic = build_semantic_renderable(text, title=title)
    if semantic:
        console.print(semantic)
        return

    paragraphs = [part.strip() for part in cleaned.split("\n\n") if part.strip()]
    lead = ""
    body = cleaned

    if paragraphs and len(paragraphs[0]) <= 220 and not paragraphs[0].startswith(("#", "-", "*", "1.")):
        lead = paragraphs[0]
        body = "\n\n".join(paragraphs[1:]).strip()

    blocks = [Rule(title=f"[{C['brand']}]{title}[/]", style=C["brand"])]
    if lead:
        blocks.append(
            Panel(
                Text(lead, style=C["text"]),
                border_style=C["brand"],
                box=box.ROUNDED,
                padding=(0, 1),
            )
        )
    if body:
        blocks.append(Padding(Markdown(body, code_theme="monokai"), (0, 1)))

    console.print(Group(*blocks))


# ── live renderer ─────────────────────────────────────────────────────────────
class LiveRenderer:
    def __init__(self):
        self.thinking_text = ""
        self.answer_text = ""
        self._thinking_printed = 0
        self._answer_printed = 0
        self._thinking_header = False
        self._thinking_done = False
        self._answer_started = False
        self._last_answer_len = 0
        self._buffer_lines = []
        self._printed_lines = set()

    def reset(self):
        self.thinking_text = ""
        self.answer_text = ""
        self._thinking_printed = 0
        self._answer_printed = 0
        self._thinking_header = False
        self._thinking_done = False
        self._answer_started = False
        self._last_answer_len = 0
        self._buffer_lines = []
        self._printed_lines = set()

    def _delta(self, old: str, new: str) -> str:
        if not new:
            return ""
        if not old:
            return new
        if new == old:
            return ""
        if new.startswith(old):
            return new[len(old):]
        # Handle case where new text is shorter (normalization changed)
        # Return the entire new text so it gets re-printed cleanly
        if len(new) < len(old):
            return new
        # If texts diverge, find common prefix and return only the new part
        # This handles cases where normalization or whitespace differs slightly
        for i in range(min(len(old), len(new)), 0, -1):
            if new.startswith(old[:i]):
                return new[i:]
        # No common prefix found, return entire new text
        return new

    def _format_paragraph(self, text: str) -> str:
        """Format a paragraph with proper line breaks."""
        # Split into lines and filter empty ones
        lines = [line.strip() for line in text.split('\n') if line.strip()]
        if not lines:
            return ""

        # Join with proper spacing
        formatted = []
        for line in lines:
            # Remove duplicate words/phrases that might appear due to extraction issues
            cleaned = re.sub(r'(\b\w+.*?)(?:\s+\1)+', r'\1', line, flags=re.IGNORECASE)
            formatted.append(cleaned)

        return '\n\n'.join(formatted)

    def _clean_duplicates(self, text: str) -> str:
        """Remove duplicate paragraphs and repeated content from extracted text."""
        if not text:
            return text

        # Split into paragraphs
        paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
        if not paragraphs:
            return text

        # Track seen paragraphs and remove duplicates
        seen = set()
        unique_paragraphs = []
        for para in paragraphs:
            # Create a normalized key for comparison
            key = re.sub(r'\s+', ' ', para.lower())
            if key not in seen:
                seen.add(key)
                unique_paragraphs.append(para)

        # Also remove repeated phrases within paragraphs
        cleaned_paragraphs = []
        for para in unique_paragraphs:
            # Remove consecutive duplicate sentences/phrases
            cleaned = re.sub(r'(.{20,}?)(?:\s+\1)+', r'\1', para, flags=re.IGNORECASE | re.DOTALL)
            cleaned_paragraphs.append(cleaned)

        return '\n\n'.join(cleaned_paragraphs)

    def update(
        self,
        thinking_text: str = "",
        answer_text: str = "",
        thinking_done: bool = False,
    ):
        thinking_text = thinking_text or ""
        answer_text = answer_text or ""

        # Track thinking state but don't print live updates
        if thinking_text and thinking_text != self.thinking_text:
            self.thinking_text = thinking_text
            self._thinking_printed = len(thinking_text)

        if thinking_done and not self._thinking_done:
            self._thinking_done = True

        # Track answer text but don't print live updates to avoid jumbled output
        if answer_text and len(answer_text) > self._last_answer_len:
            self.answer_text = answer_text
            self._last_answer_len = len(answer_text)
            self._answer_started = True

    def finish(self, render_output: bool = True):
        """Finish rendering and display final formatted output."""
        if render_output and self.answer_text:
            cleaned = self._clean_duplicates(self.answer_text)
            render_response(cleaned)
        elif render_output and self.thinking_text:
            console.print(f"[{C['dim']}]No final answer text was captured.[/]")
