"""
dream/train_distillation.py — Consume distillation_data.json produced by Dream.
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path

logger = logging.getLogger("dream.train")
logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")


def load_dataset(path: str) -> list[dict]:
    with open(path) as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("Expected a JSON array at top level")
    logger.info("Loaded %d samples from %s", len(data), path)
    return data


def train_ollama(samples: list[dict], base_model: str, output_name: str) -> None:
    knowledge_block = "\n".join(f"- {s['input']}" for s in samples[:200])
    modelfile = f"""FROM {base_model}

SYSTEM \"\"\"
You are a knowledgeable assistant with the following verified domain knowledge:

{knowledge_block}

Use this knowledge to give accurate, precise answers.
\"\"\"
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".Modelfile", delete=False) as f:
        f.write(modelfile)
        modelfile_path = f.name

    logger.info("Modelfile written to %s", modelfile_path)
    cmd = ["ollama", "create", output_name, "-f", modelfile_path]
    logger.info("Running: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    os.unlink(modelfile_path)

    if result.returncode != 0:
        logger.error("ollama create failed:\n%s", result.stderr)
        sys.exit(1)
    logger.info("Model %r created successfully.", output_name)


def train_unsloth(samples: list[dict], base_model: str, output_dir: str) -> None:
    try:
        from unsloth import FastLanguageModel
        from datasets import Dataset
        from trl import SFTTrainer
        from transformers import TrainingArguments
    except ImportError:
        logger.error("Unsloth or training dependencies not installed")
        sys.exit(1)

    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=base_model,
        max_seq_length=2048,
        load_in_4bit=True,
    )
    model = FastLanguageModel.get_peft_model(
        model,
        r=16,
        target_modules=["q_proj", "v_proj"],
        lora_alpha=16,
        lora_dropout=0.05,
    )

    def format_sample(s):
        return {
            "text": (
                f"### Instruction:\n{s['instruction']}\n\n"
                f"### Input:\n{s['input']}\n\n"
                f"### Response:\n{s['output']}"
            )
        }

    dataset = Dataset.from_list([format_sample(s) for s in samples])

    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=dataset,
        dataset_text_field="text",
        max_seq_length=2048,
        args=TrainingArguments(
            per_device_train_batch_size=2,
            gradient_accumulation_steps=4,
            warmup_steps=10,
            num_train_epochs=3,
            learning_rate=2e-4,
            fp16=True,
            output_dir=output_dir,
            save_strategy="epoch",
            logging_steps=10,
        ),
    )
    trainer.train()
    model.save_pretrained(output_dir)
    tokenizer.save_pretrained(output_dir)
    logger.info("LoRA adapter saved to %s", output_dir)


def main():
    parser = argparse.ArgumentParser(description="Train a model from Dream distillation data")
    parser.add_argument("--input", required=True, help="Path to distillation_data.json")
    parser.add_argument("--method", choices=["ollama", "unsloth"], default="ollama")
    parser.add_argument("--base-model", default="qwen2.5:7b", help="Base model name")
    parser.add_argument("--output", default=None, help="Output model name or directory")
    args = parser.parse_args()

    samples = load_dataset(args.input)
    if not samples:
        logger.error("No samples found in dataset — nothing to train on.")
        sys.exit(1)

    if args.method == "ollama":
        output_name = args.output or f"dream-{Path(args.input).stem}"
        train_ollama(samples, args.base_model, output_name)
    else:
        output_dir = args.output or "dream_lora_adapter"
        train_unsloth(samples, args.base_model, output_dir)


if __name__ == "__main__":
    main()