# Stored voice runs

These files are the measurements of runs 1–9 in #457 and #462, made on linux-5090 on 2026-11-28. They are copied unchanged from the scripts that made them. Never edit them: a new measurement goes into a new folder (guardrail 6).

| Folder | Records | Blind set, key, judgements | Judge |
|---|---|---|---|
| `run1` | `run1.jsonl` (375 comments) | none: scored by the word check only | none |
| `run2` | `run2-board.jsonl` (285) | in `run2-3-blind` | |
| `run3` | `run3-roles.jsonl` (285) | in `run2-3-blind` | |
| `run2-3-blind` | | `blind-*`: sample 0 of every cell of runs 2 and 3 (190) | session 1 |
| `run4` | `run4.jsonl` (150) | `blind4-*` (150) | session 1 |
| `run5` | `run5.jsonl` (150), `run5-12b.jsonl` (150) | `blind5-*`, `blind5b-*` (150 each) | session 1 |
| `run6` | `run6.jsonl` (100) | `blind6-*` (100) | session 1 |
| `run7` | `run7-tal.jsonl` (100) | `blind7-*` (100) | session 1 |
| `run8` | `run8-tal.jsonl` (100) | `blind8-*` (100) | session 1 |
| `run8-models` | `run8-<model>.jsonl` (11 models, 50 each), `models-vram.txt` | `blindm0-*`, `blindm1-*` (300 each), `blindm-key.json` | sessions 2 and 3, one half each |
| `run9` | `run9-<cell>.jsonl` (5 cells, 50 each) | `blind9-*` (250) | session 4 |
| `vlm` | the 5 boards drawn for the vision-model test (#457, finding 6) | none: the model's replies were not stored | none |

Each folder with a blind set also holds the judge brief, word for word, as `judge-brief-<set>.md`.

Limits of these records:

- They predate the `meta` field. They do not record the git commit, the model digest, the Ollama version or the sampling options. The model behind each file is named in #457 and in `summarise.py`. Run 9 records name the model and the options. New runs record all of it.
- One judge session (session 1) judged the sets of runs 2–8 one after the other. Each set was blind, but the judge knew the rubric decisions it had made on the earlier sets.
- Judge session 4 ran on Claude Sonnet. Sessions 1–3 ran on the default model for subagents, and the session log does not name it. The comments themselves come from the local model of each run.
- `models-vram.txt` lacks gemma4-e4b Q3_K_M. Its 2.43 GiB comes from #457 and is written in `summarise.py`.
