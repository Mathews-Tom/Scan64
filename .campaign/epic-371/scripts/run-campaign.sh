#!/usr/bin/env bash
# Run Campaign: epic-371
# launch → poll → verify → merge into epic/371 → check → push → next wave → draft PR to main
set -euo pipefail

PROJECT_DIR="D:/projects/Scan64"
CAMPAIGN_DIR="${PROJECT_DIR}/.campaign/epic-371"
BASE_BRANCH="epic/371"
POLL_INTERVAL="${1:-120}"
START_WAVE="${START_WAVE:-1}"
export PATH="${HOME}/.local/opt/node24:${HOME}/.local/bin:${PATH}"
export SCAN64_STOCKFISH_PATH="${HOME}/.local/bin/stockfish.exe"

# Many-core hosts: the default batch pool (CPUs - 3) starts ~45 engines and breaks 5 s job tests.
export SCAN64_ENGINE_POOL_BATCH_CONCURRENCY="${SCAN64_ENGINE_POOL_BATCH_CONCURRENCY:-2}"

cd "${PROJECT_DIR}"
mkdir -p "${CAMPAIGN_DIR}/logs" "${CAMPAIGN_DIR}/.junk/worktrees"

# ── Base branch ────────────────────────────────────────────
git fetch origin
if ! git show-ref --verify --quiet "refs/heads/${BASE_BRANCH}"; then
  git branch "${BASE_BRANCH}" origin/main
fi
git checkout "${BASE_BRANCH}"
git ls-remote --exit-code --heads origin "${BASE_BRANCH}" >/dev/null || git push -u origin "${BASE_BRANCH}"

poll_wave() {
  echo "Polling every ${POLL_INTERVAL}s for: $*"
  while true; do
    local all_done=true
    for id in "$@"; do
      [ -f "${CAMPAIGN_DIR}/.markers/terminal-${id}.done" ] || { all_done=false; break; }
    done
    $all_done && return 0
    sleep "${POLL_INTERVAL}"
  done
}

check_status() {
  local id="$1" status
  status=$(grep -m1 '^status:' "${CAMPAIGN_DIR}/progress/terminal-${id}.md" | awk '{print $2}')
  if [ "${status}" != "complete" ]; then
    echo "STOP: Terminal ${id} exited with status '${status}'"
    echo "  Check:  cat ${CAMPAIGN_DIR}/progress/terminal-${id}.md"
    echo "  Resume: /campaign resume ${id} epic-371, then START_WAVE=<n> bash $0"
    exit 1
  fi
}

merge_branch() {
  local id="$1" branch
  branch=$(grep -m1 '^branch:' "${CAMPAIGN_DIR}/progress/terminal-${id}.md" | awk '{print $2}')
  echo "Merging ${branch} → ${BASE_BRANCH}..."
  git fetch origin "${branch}"
  if ! git merge --no-ff --signoff -m "Merge ${branch} into ${BASE_BRANCH}" "origin/${branch}"; then
    echo "STOP: merge conflict on ${branch}. Resolve, commit with -s, then START_WAVE=<next> bash $0"
    exit 1
  fi
  [ -d "D:/projects/scan64-wt/terminal-${id}" ] && mv "D:/projects/scan64-wt/terminal-${id}" "${CAMPAIGN_DIR}/.junk/worktrees/terminal-${id}.merged.$(date +%Y%m%d_%H%M%S)"
  git worktree prune
}

gate() {
  local wave="$1"
  uv sync --locked >/dev/null && pnpm install --frozen-lockfile >/dev/null
  # Windows host: deselect only the tests that fail on the unchanged base here (see file).
  local deselect=""
  while IFS= read -r t; do [ -n "${t}" ] && deselect="${deselect} --deselect=${t}"; done \
    <"${CAMPAIGN_DIR}/windows-baseline-deselect.txt"
  if ! PYTEST_ADDOPTS="${deselect}" scripts/check.sh >"${CAMPAIGN_DIR}/logs/check-wave-${wave}.log" 2>&1; then
    echo "STOP: scripts/check.sh failed after wave ${wave}. See ${CAMPAIGN_DIR}/logs/check-wave-${wave}.log"
    exit 1
  fi
  git push origin "${BASE_BRANCH}"
}

run_wave() {
  local wave="$1"; shift
  [ "${wave}" -lt "${START_WAVE}" ] && { echo "Skipping wave ${wave}"; return; }
  echo "=== Wave ${wave} ==="
  if [ "${POLL_ONLY_WAVE:-0}" = "${wave}" ]; then
    echo "Wave ${wave} already launched; polling only."
  else
    bash "${CAMPAIGN_DIR}/scripts/launch-wave-${wave}.sh"
  fi
  poll_wave "$@"
  for id in "$@"; do check_status "${id}"; done
  git checkout "${BASE_BRANCH}"
  for id in "$@"; do merge_branch "${id}"; done
  gate "${wave}"
  echo "Wave ${wave} complete, checked, and pushed."
}

run_wave 1 A B C D
run_wave 2 E F G
run_wave 3 I

# ── Draft PR epic/371 → main ───────────────────────────────
if ! gh pr view "${BASE_BRANCH}" -R Mathews-Tom/Scan64 >/dev/null 2>&1; then
  gh pr create -R Mathews-Tom/Scan64 --draft --base main --head "${BASE_BRANCH}" \
    --title "Epic #371: FIDE board and pieces, swappable engine, grounded narrator" \
    --body "$(printf 'Merges the epic/371 campaign branch.\n\nCloses #359\nCloses #360\nCloses #361\nCloses #363\nCloses #366\nCloses #367\nCloses #368\nCloses #369\n\nPart of #371\n\nhttps://claude.ai/code/session_01FfK5BwEQYfqBv6c3jN3tHQ')"
fi
echo "Campaign epic-371 complete."
