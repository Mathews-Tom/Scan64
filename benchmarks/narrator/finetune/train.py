"""LoRA fine-tune on the examples of ``data.py``; merges the adapter into the base (#465).

Runs in the training environment (``requirements.txt``), not in Scan64's:

    python train.py --data train.jsonl --base <snapshot dir> --out <dir> --report report.json
"""

import argparse
import hashlib
import json
import math
import random
import tomllib
from pathlib import Path

import torch
from peft import LoraConfig, get_peft_model
from transformers import AutoModelForCausalLM, AutoTokenizer

IGNORE = -100


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def encode(tokenizer, messages):
    """Input ids and labels; only the sentence (and its end token) carries loss."""
    prompt = tokenizer.apply_chat_template(
        messages[:-1], tokenize=False, add_generation_prompt=True, enable_thinking=False
    )
    target = messages[-1]["content"] + "<|im_end|>"
    prompt_ids = tokenizer(prompt, add_special_tokens=False)["input_ids"]
    target_ids = tokenizer(target, add_special_tokens=False)["input_ids"]
    return prompt_ids + target_ids, [IGNORE] * len(prompt_ids) + target_ids


def batch_loss(model, rows, pad):
    width = max(len(ids) for ids, _ in rows)
    ids = torch.tensor([i + [pad] * (width - len(i)) for i, _ in rows], device="cuda")
    labels = torch.tensor([t + [IGNORE] * (width - len(t)) for _, t in rows], device="cuda")
    mask = torch.tensor([[1] * len(i) + [0] * (width - len(i)) for i, _ in rows], device="cuda")
    return model(input_ids=ids, attention_mask=mask, labels=labels).loss


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    config = tomllib.loads((Path(__file__).parent / "finetune.toml").read_text(encoding="utf-8"))
    settings, lora = config["train"], config["lora"]
    rows_raw = [json.loads(line) for line in args.data.read_text(encoding="utf-8").splitlines()]
    if not rows_raw:
        raise SystemExit("no training examples")
    random.seed(settings["seed"])
    torch.manual_seed(settings["seed"])
    tokenizer = AutoTokenizer.from_pretrained(args.base)
    rows = [encode(tokenizer, row["messages"]) for row in rows_raw]
    model = AutoModelForCausalLM.from_pretrained(args.base, dtype=torch.bfloat16).to("cuda")
    model = get_peft_model(
        model,
        LoraConfig(
            r=lora["rank"],
            lora_alpha=lora["alpha"],
            lora_dropout=lora["dropout"],
            target_modules=lora["targets"],
            task_type="CAUSAL_LM",
        ),
    )
    size = settings["batch"]
    steps = settings["epochs"] * math.ceil(len(rows) / size)
    optimizer = torch.optim.AdamW(
        [p for p in model.parameters() if p.requires_grad], lr=settings["learning_rate"]
    )
    schedule = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, steps)
    pad = tokenizer.pad_token_id

    def full_loss() -> float:
        model.eval()
        with torch.no_grad():
            return float(batch_loss(model, rows, pad))

    before, losses = full_loss(), []
    for _ in range(settings["epochs"]):
        random.shuffle(rows)
        for start in range(0, len(rows), size):
            model.train()
            loss = batch_loss(model, rows[start : start + size], pad)
            loss.backward()
            optimizer.step()
            schedule.step()
            optimizer.zero_grad()
            losses.append(float(loss))
    after = full_loss()
    peak = torch.cuda.max_memory_allocated()
    args.out.mkdir(parents=True)
    model.save_pretrained(args.out / "adapter")
    merged = model.merge_and_unload()
    merged.save_pretrained(args.out / "merged")
    tokenizer.save_pretrained(args.out / "merged")
    report = {
        "examples": len(rows),
        "optimizer_steps": steps,
        "seed": settings["seed"],
        "loss_before": before,
        "losses": losses,
        "loss_after": after,
        "peak_vram_bytes": peak,
        "torch": torch.__version__,
        "gpu": torch.cuda.get_device_name(0),
        "train_jsonl_sha256": sha256(args.data),
        "config_sha256": sha256(Path(__file__).parent / "finetune.toml"),
        "base_sha256": {p.name: sha256(p) for p in sorted(args.base.glob("*.safetensors"))},
        "output_sha256": {
            f"{p.parent.name}/{p.name}": sha256(p) for p in sorted(args.out.glob("*/*.safetensors"))
        },
    }
    args.report.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
