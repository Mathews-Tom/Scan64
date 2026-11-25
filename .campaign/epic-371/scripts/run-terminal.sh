#!/usr/bin/env bash
# Generic terminal runner for campaign epic-371 (Windows / Git Bash).
# Usage: run-terminal.sh <ID> <BRANCH> <ISSUES> <WRITES> [resume]
set -uo pipefail

ID="$1"; BRANCH="$2"; ISSUES="$3"; WRITES="$4"; MODE="${5:-fresh}"
ROOT="D:/projects/Scan64"
C="${ROOT}/.campaign/epic-371"
# Worktrees live outside the repo to keep node_modules paths under the Windows path limit.
WT_ROOT="D:/projects/scan64-wt"
WT="${WT_ROOT}/terminal-${ID}"
LOG="${C}/logs/terminal-${ID}.log"
mkdir -p "${C}/logs" "${C}/.markers" "${WT_ROOT}" "${C}/.junk/worktrees"
export PATH="${HOME}/.local/opt/node24:${HOME}/.local/bin:${PATH}"
export SCAN64_STOCKFISH_PATH="${HOME}/.local/bin/stockfish.exe"

# Many-core hosts: the default batch pool (CPUs - 3) starts many engines and breaks 5 s job tests.
export SCAN64_ENGINE_POOL_BATCH_CONCURRENCY="${SCAN64_ENGINE_POOL_BATCH_CONCURRENCY:-2}"

trap 'touch "${C}/.markers/terminal-${ID}.done"' EXIT

if [ "${MODE}" = "resume" ] && [ -d "${WT}" ]; then
  cd "${WT}"
else
  if [ -d "${WT}" ]; then
    mv "${WT}" "${C}/.junk/worktrees/terminal-${ID}.$(date +%Y%m%d_%H%M%S)"
  fi
  git -C "${ROOT}" worktree prune
  ok=0
  for try in 1 2 3 4 5; do
    if git -C "${ROOT}" worktree add -B "${BRANCH}" "${WT}" epic/371 >>"${LOG}" 2>&1; then ok=1; break; fi
    sleep $((try * 3))
  done
  [ "${ok}" = 1 ] || { echo "worktree add failed; see ${LOG}"; sed -i 's/^status: .*/status: failed/' "${C}/progress/terminal-${ID}.md"; exit 1; }
  cd "${WT}"
fi

{ uv sync --locked && pnpm install --frozen-lockfile; } >"${C}/logs/terminal-${ID}-setup.log" 2>&1 || {
  echo "Dependency setup failed; see ${C}/logs/terminal-${ID}-setup.log"
  sed -i 's/^status: .*/status: failed/' "${C}/progress/terminal-${ID}.md"
  exit 1
}

PREFIX=""
[ "${MODE}" = "resume" ] && [ -f "${C}/prompts/resume-${ID}.md" ] && PREFIX="$(cat "${C}/prompts/resume-${ID}.md")"

PROMPT="${PREFIX}
$(cat "${C}/prompts/task-${ID}.md")

$(cat "${C}/prompts/context.md")
$(sed -e "s|{{ID}}|${ID}|g" -e "s|{{BRANCH}}|${BRANCH}|g" -e "s|{{ISSUES}}|${ISSUES}|g" -e "s|{{WRITES}}|${WRITES}|g" "${C}/prompts/protocol.md")"

claude --model claude-opus-5-5 --dangerously-skip-permissions --no-session-persistence \
  --add-dir "${C}/progress" -p "${PROMPT}" 2>&1 | tee -a "${LOG}"
