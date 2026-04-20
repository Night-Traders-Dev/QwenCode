"""
mpk_server.py — OpenAI-compatible /v1/chat/completions server
backed by Mirage MPK (qwen3.5:0.8b).

Usage:
    python mpk_server.py [--port 11435] [--no-mpk]
"""

import argparse
import asyncio
import json
import logging
import sys
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import torch
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from transformers import AutoModelForCausalLM, AutoTokenizer

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(name)s | %(message)s")
logger = logging.getLogger("mpk_server")

MODEL_ID = "Qwen/Qwen3.5-0.8B"
_model = None
_tokenizer = None
_mpk = None
_use_mpk = False
_generate_lock = asyncio.Lock()


# ── Model loading ─────────────────────────────────────────────────────────────

def _get_sm_config(num_sms: int) -> dict:
    num_local_schedulers = 16
    num_workers = num_sms - 4
    assert num_workers > 0, f"Too few SMs ({num_sms})"
    return {"num_workers": num_workers, "num_local_schedulers": num_local_schedulers}


def _load_model():
    global _model, _tokenizer
    logger.info("Loading %s...", MODEL_ID)
    _tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, trust_remote_code=True)
    _model = AutoModelForCausalLM.from_pretrained(
        MODEL_ID,
        torch_dtype=torch.bfloat16,
        device_map="cuda",
        trust_remote_code=True,
    )
    _model.eval()
    logger.info("Model loaded.")


def _try_compile_mpk(max_new_tokens: int = 512) -> bool:
    global _mpk
    try:
        import mirage as mi
    except ImportError:
        logger.warning("Mirage not importable — using eager inference.")
        return False

    num_sms = torch.cuda.get_device_properties(0).multi_processor_count
    sm_cfg  = _get_sm_config(num_sms)
    logger.info("Compiling MPK | SMs=%d workers=%d schedulers=%d",
                num_sms, sm_cfg["num_workers"], sm_cfg["num_local_schedulers"])

    step   = torch.zeros(1, dtype=torch.int32, device="cuda")
    tokens = torch.zeros(1, max_new_tokens + 128, dtype=torch.int64, device="cuda")

    try:
        mpk = mi.PersistentKernel(
            world_size=1, mpi_rank=0,
            num_workers=sm_cfg["num_workers"],
            num_local_schedulers=sm_cfg["num_local_schedulers"],
            num_remote_schedulers=0,
            meta_tensors=[step, tokens],
            profiler_tensor=None,
        )
        for i, layer in enumerate(_model.model.layers):
            mpk.attach_input(layer.input_layernorm.weight,          name=f"input_norm_{i}")
            mpk.attach_input(layer.post_attention_layernorm.weight,  name=f"post_attn_norm_{i}")
            mpk.attach_input(layer.self_attn.q_proj.weight,         name=f"q_{i}")
            mpk.attach_input(layer.self_attn.k_proj.weight,         name=f"k_{i}")
            mpk.attach_input(layer.self_attn.v_proj.weight,         name=f"v_{i}")
            mpk.attach_input(layer.self_attn.o_proj.weight,         name=f"o_{i}")
            mpk.attach_input(layer.mlp.gate_proj.weight,            name=f"gate_{i}")
            mpk.attach_input(layer.mlp.up_proj.weight,              name=f"up_{i}")
            mpk.attach_input(layer.mlp.down_proj.weight,            name=f"down_{i}")
        mpk.attach_input(_model.model.norm.weight, name="final_norm")
        mpk.attach_input(_model.lm_head.weight,    name="lm_head")
        mpk.compile()
        _mpk = {"kernel": mpk, "step": step, "tokens": tokens}
        logger.info("MPK compiled successfully.")
        return True
    except Exception as exc:
        logger.warning("MPK compile failed (%s) — falling back to eager.", exc)
        return False


# ── Inference ─────────────────────────────────────────────────────────────────

def _messages_to_prompt(messages: list[dict]) -> str:
    """Convert OpenAI message list to a plain prompt string."""
    parts = []
    for msg in messages:
        role    = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "system":
            parts.append(f"<|system|>\n{content}")
        elif role == "assistant":
            parts.append(f"<|assistant|>\n{content}")
        else:
            parts.append(f"<|user|>\n{content}")
    parts.append("<|assistant|>")
    return "\n".join(parts)


def _infer_eager(prompt: str, max_tokens: int, temperature: float) -> str:
    input_ids = _tokenizer.encode(prompt, return_tensors="pt").cuda()
    with torch.no_grad():
        output = _model.generate(
            input_ids,
            max_new_tokens=max_tokens,
            do_sample=temperature > 0.01,
            temperature=max(temperature, 0.01),
            pad_token_id=_tokenizer.eos_token_id,
        )
    new_ids = output[0][input_ids.shape[1]:]
    return _tokenizer.decode(new_ids, skip_special_tokens=True)


def _infer_mpk(prompt: str, max_tokens: int) -> str:
    kernel  = _mpk["kernel"]
    step    = _mpk["step"]
    tokens  = _mpk["tokens"]

    input_ids = _tokenizer.encode(prompt, return_tensors="pt").cuda()
    seq_len   = input_ids.shape[1]

    # Reset buffers
    tokens.zero_()
    tokens[0, :seq_len] = input_ids[0]
    step[0] = seq_len

    generated = input_ids[0].tolist()
    for _ in range(max_tokens):
        nxt = kernel.run()
        tok = int(nxt.item()) if hasattr(nxt, "item") else int(nxt)
        generated.append(tok)
        if tok == _tokenizer.eos_token_id:
            break
        cur = int(step[0].item())
        if cur < tokens.shape[1]:
            tokens[0, cur] = tok
        step[0] = cur + 1

    new_tokens = generated[seq_len:]
    return _tokenizer.decode(new_tokens, skip_special_tokens=True)


async def _infer(prompt: str, max_tokens: int, temperature: float) -> tuple[str, str]:
    """Returns (text, backend_used)."""
    async with _generate_lock:   # MPK is stateful — serialize all requests
        if _use_mpk and _mpk is not None:
            try:
                t0  = time.perf_counter()
                txt = await asyncio.to_thread(_infer_mpk, prompt, max_tokens)
                dt  = time.perf_counter() - t0
                n   = len(_tokenizer.encode(txt))
                logger.info("[mpk]   %d tok | %.2fs | %.1f tok/s", n, dt, n / max(dt, 1e-6))
                return txt, "mpk"
            except Exception as exc:
                logger.warning("[mpk] inference failed (%s) — falling back to eager", exc)

        t0  = time.perf_counter()
        txt = await asyncio.to_thread(_infer_eager, prompt, max_tokens, temperature)
        dt  = time.perf_counter() - t0
        n   = len(_tokenizer.encode(txt))
        logger.info("[eager] %d tok | %.2fs | %.1f tok/s", n, dt, n / max(dt, 1e-6))
        return txt, "eager"


# ── FastAPI app ───────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    global _use_mpk
    _load_model()
    if _use_mpk:
        _use_mpk = _try_compile_mpk()
    yield


app = FastAPI(title="MPK OpenAI Server", lifespan=lifespan)


# ── Pydantic models ───────────────────────────────────────────────────────────

class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    model: str = MODEL_ID
    messages: list[ChatMessage]
    max_tokens: int = 512
    temperature: float = 0.7
    stream: bool = False
    response_format: dict[str, Any] | None = None


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/v1/models")
async def list_models():
    return {
        "object": "list",
        "data": [{
            "id": MODEL_ID,
            "object": "model",
            "created": int(time.time()),
            "owned_by": "local",
        }],
    }


@app.post("/v1/chat/completions")
async def chat_completions(req: ChatRequest):
    messages = [m.model_dump() for m in req.messages]
    prompt   = _messages_to_prompt(messages)

    # json_object mode: append instruction
    if (req.response_format or {}).get("type") == "json_object":
        prompt += "\nRespond with valid JSON only."

    try:
        text, backend = await _infer(prompt, req.max_tokens, req.temperature)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    response_body = {
        "id": completion_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": req.model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": text},
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens":     len(_tokenizer.encode(prompt)),
            "completion_tokens": len(_tokenizer.encode(text)),
            "total_tokens":      len(_tokenizer.encode(prompt)) + len(_tokenizer.encode(text)),
        },
        "system_fingerprint": backend,
    }

    if req.stream:
        async def event_stream():
            chunk = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": req.model,
                "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": "stop"}],
            }
            yield f"data: {json.dumps(chunk)}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(event_stream(), media_type="text/event-stream")

    return JSONResponse(content=response_body)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "backend": "mpk" if (_use_mpk and _mpk) else "eager",
        "device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu",
    }


# ── Entrypoint ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port",   type=int, default=11435)
    parser.add_argument("--host",   default="127.0.0.1")
    parser.add_argument("--no-mpk", action="store_true")
    args = parser.parse_args()

    _use_mpk = not args.no_mpk

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")