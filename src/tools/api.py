import json
import sys
from typing import Any
from openai import OpenAI, APIError, APIConnectionError
from tools.definitions import TOOLS
from tools.tools import dispatch_tool, print_tool_call, print_tool_result
from ui.rich_ui import console
from ui.live_render import C
from config.config import MAX_TOOL_ITERS

# ── streaming completion (API mode) ───────────────────────────────────────────
def stream_completion(
    client: OpenAI, cfg: dict, messages: list
) -> tuple[str, list]:
    full_text = ""
    tool_call_accum: dict[int, dict] = {}

    console.print(f"\n[{C['brand']}◆ Qwen Coder[/] ", end="")

    with client.chat.completions.create(
        model=cfg["model"],
        messages=messages,
        tools=TOOLS,
        tool_choice="auto",
        temperature=cfg["temperature"],
        max_tokens=cfg["max_tokens"],
        stream=True,
    ) as stream:
        for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta is None:
                continue
            if delta.content:
                console.print(delta.content, end="", markup=False)
                full_text += delta.content
            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    if idx not in tool_call_accum:
                        tool_call_accum[idx] = {"id": "", "name": "", "args": ""}
                    if tc.id:
                        tool_call_accum[idx]["id"] += tc.id
                    if tc.function and tc.function.name:
                        tool_call_accum[idx]["name"] += tc.function.name
                    if tc.function and tc.function.arguments:
                        tool_call_accum[idx]["args"] += tc.function.arguments

    console.print()
    tool_calls = [
        {
            "id":        acc["id"] or f"call_{idx}",
            "name":      acc["name"],
            "args_json": acc["args"],
        }
        for idx, acc in sorted(tool_call_accum.items())
    ]
    return full_text, tool_calls

def agentic_turn_api(
    client: OpenAI, cfg: dict, messages: list
) -> list:
    for iteration in range(MAX_TOOL_ITERS):
        text, tool_calls = stream_completion(client, cfg, messages)

        if not tool_calls:
            if text and not text.endswith("\n"):
                console.print()
            messages.append({"role": "assistant", "content": text or ""})
            return messages

        assistant_msg: dict[str, Any] = {
            "role":    "assistant",
            "content": text or "",          # never None — some endpoints reject it
            "tool_calls": [
                {
                    "id":       tc["id"],
                    "type":     "function",
                    "function": {
                        "name":      tc["name"],
                        "arguments": tc["args_json"],
                    },
                }
                for tc in tool_calls
            ],
        }
        messages.append(assistant_msg)

        console.print(f"\n[{C['accent']}⚙  Tools[/]")
        tool_results = []
        for tc in tool_calls:
            try:
                args = json.loads(tc["args_json"] or "{}")
            except json.JSONDecodeError:
                args = {}
            print_tool_call(tc["name"], args)
            result = dispatch_tool(tc["name"], args)
            print_tool_result(result, ok=not result.startswith("[error]"))
            tool_results.append({
                "role":         "tool",
                "tool_call_id": tc["id"],
                "content":      result,
            })
        messages.extend(tool_results)

    console.print(
        f"[{C['warn']}]⚠  Reached max tool iterations ({MAX_TOOL_ITERS})[/]"
    )
    return messages
