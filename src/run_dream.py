#!/usr/bin/env python3
"""
run_dream.py — CLI entrypoint for the Dream training loop.

Examples:
    python run_dream.py "Transformer neural architectures" --hours 4
    python run_dream.py "RISC-V instruction set" --hours 8 --questions 15
    python run_dream.py "Vulkan memory model" --cloud-model "qwen3:6-35b-a3b"

Resume a stopped session (same topic + memory path):
    python run_dream.py "RISC-V instruction set" --hours 8 --resume
"""

import argparse
import asyncio
import sys

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent))

from dream.config import DreamConfig, ModelConfig
from dream.session import DreamSession


def build_config(args: argparse.Namespace) -> DreamConfig:
    cfg = DreamConfig()
    cfg.target_duration_hours = args.hours
    cfg.questions_per_test = args.questions
    cfg.memory_path = args.memory or f"dream_{args.topic[:30].replace(' ', '_')}.json"

    if args.cloud_model:
        cfg.cloud.name = args.cloud_model
    if args.medium_model:
        cfg.medium.name = args.medium_model
    if args.small_model:
        cfg.small.name = args.small_model

    if args.cloud_url:
        cfg.cloud.base_url = args.cloud_url
    if args.cloud_key:
        cfg.cloud.api_key = args.cloud_key

    return cfg


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Dream — multi-agent self-supervised learning loop"
    )
    parser.add_argument("topic", help="Subject for the dream session")
    parser.add_argument("--hours", type=float, default=4.0, help="Session duration (default 4h)")
    parser.add_argument("--questions", type=int, default=10, help="Questions per test cycle")
    parser.add_argument("--memory", type=str, default=None, help="Path to memory JSON file")
    parser.add_argument("--resume", action="store_true", help="Resume from existing memory file")

    # Model overrides
    parser.add_argument("--cloud-model", type=str, default=None)
    parser.add_argument("--medium-model", type=str, default=None)
    parser.add_argument("--small-model", type=str, default=None)

    # Cloud endpoint (if using a non-local Ollama or OpenAI-compat endpoint)
    parser.add_argument("--cloud-url", type=str, default=None)
    parser.add_argument("--cloud-key", type=str, default=None)

    args = parser.parse_args()
    cfg = build_config(args)

    print(f"\n🌙  DREAM — {args.topic}")
    print(f"    Duration : {args.hours}h")
    print(f"    Cloud    : {cfg.cloud.name}")
    print(f"    Medium   : {cfg.medium.name}")
    print(f"    Small    : {cfg.small.name}")
    print(f"    Memory   : {cfg.memory_path}\n")

    session = DreamSession(topic=args.topic, config=cfg)
    try:
        asyncio.run(session.run())
    except KeyboardInterrupt:
        print("\n\nStopped. Progress saved.")
        sys.exit(0)


if __name__ == "__main__":
    main()
