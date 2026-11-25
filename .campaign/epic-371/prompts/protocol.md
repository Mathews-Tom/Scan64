
--- PROGRESS PROTOCOL ---
CRITICAL FIRST ACTION: Read /home/syd/projects/Scan64/.campaign/epic-371/progress/terminal-{{ID}}.md.
Set `status: in_progress` and `started:` to the current ISO time.
After every commit, update that file:
- The Issues table Actual column with what now exists (counts, files, test results).
- A timestamped Log entry that says what changed.
- `updated:` to the current ISO time.
On completion: set `status: complete`, set each issue row Status to `done`, and write the Results section (what shipped, test commands run and their results, the PR URL, open follow-ups).
On a hard failure: set `status: failed` and log the error and the last good commit.
This file lives outside your worktree (absolute path above). Do not commit it.

--- COORDINATION RULES ---
- Additive only: never remove an existing theme, piece set, setting, endpoint, or stored-data format.
- The engine judges; the LLM only words engine and board facts. Never let an LLM decide a chess fact.
- Sign off every commit with `git commit -s` (DCO). Use conventional prefixes: feat(scope):, fix(scope):, docs(scope):, test(scope):.
- Write only to your targets: {{WRITES}}. Read anything.
- Do NOT modify: Dockerfile, scan64-server.spec, scripts/build-server.py, scripts/release/, apps/scan64-desktop/src-tauri/, CHANGELOG.md.
- Make atomic commits. Push the branch after each unit of work: `git push -u origin {{BRANCH}}`.
- After the first push, open a PR: `gh pr create --base epic/371 --head {{BRANCH}} --title "<conventional title>" --body "<summary>\n\nPart of {{ISSUES}}"`. Never use Closes/Fixes keywords. Never target main. Never merge the PR yourself.
- Run the tests for the area you change: `uv run pytest <paths> -m "not real_model"` for Python; `pnpm --dir apps/scan64-web test` and `pnpm --dir apps/scan64-web lint` for the web client.
- Before the final push, run `scripts/check.sh`. Fix failures your change causes. Record any pre-existing failure in the progress file with its output.
- Update user and architecture docs when behaviour changes. Make no claims of measured chess improvement.
- Never put secrets, tokens, or personal email addresses in code, logs, commits, or PRs.
- Do not wait for input; no human answers during the run. Make the call, record it in the PR body, and continue.
- When done: post a short summary comment on each issue with `gh issue comment <N> --body ...` linking the PR. Do not close issues.

Read AGENTS.md or CLAUDE.md if present, CONTRIBUTING.md, docs/system-design.md, and /home/syd/projects/Scan64/.campaign/epic-371/campaign.toml for full context.
