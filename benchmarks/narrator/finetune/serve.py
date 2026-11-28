"""Serve one Hugging Face model on the Ollama endpoints the evaluation uses (#465).

Runs in the training environment. The weights stay bf16 and unquantized, so GPU memory and
latency here are not those of a packaged GGUF.

    python serve.py --model <dir> --name scan64-ft-tuned --port 11499
"""

import argparse
import hashlib
import json
import tomllib
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import torch
import transformers
from transformers import AutoModelForCausalLM, AutoTokenizer


def digest(folder: Path) -> str:
    total = hashlib.sha256()
    for path in sorted(folder.glob("*.safetensors")):
        with path.open("rb") as handle:
            while chunk := handle.read(1 << 20):
                total.update(chunk)
    return total.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    config = tomllib.loads((Path(__file__).parent / "finetune.toml").read_text(encoding="utf-8"))
    sampling, sha = config["serve"], digest(args.model)
    torch.manual_seed(config["train"]["seed"])
    tokenizer = AutoTokenizer.from_pretrained(args.model)
    model = AutoModelForCausalLM.from_pretrained(args.model, dtype=torch.bfloat16).to("cuda").eval()

    def chat(messages: list[dict[str, str]]) -> str:
        prompt = tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True, enable_thinking=False
        )
        ids = tokenizer(prompt, add_special_tokens=False, return_tensors="pt").to("cuda")
        with torch.no_grad():
            out = model.generate(**ids, max_new_tokens=256, do_sample=True, **sampling)
        text = tokenizer.decode(out[0][ids["input_ids"].shape[1] :], skip_special_tokens=True)
        return text.strip()

    class Handler(BaseHTTPRequestHandler):
        def reply(self, value: object) -> None:
            body = json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            entry = {
                "name": args.name,
                "digest": sha,
                "size_vram": torch.cuda.max_memory_allocated(),
            }
            if self.path == "/api/version":
                self.reply({"version": f"transformers {transformers.__version__}"})
            else:  # /api/tags and /api/ps
                self.reply({"models": [entry]})

        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.reply({"message": {"role": "assistant", "content": chat(body["messages"])}})

        def log_message(self, *_: object) -> None:
            pass

    HTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
