#!/usr/bin/env bash
# Launch Wave 3 — Features (tmux). Set NO_ATTACH=1 to skip attaching.
set -euo pipefail
C="/home/syd/projects/Scan64/.campaign/epic-371"
SESSION="campaign-epic-371-w3"
mkdir -p "${C}/.markers"
rm -f "${C}/.markers/terminal-I.done"
tmux kill-session -t "${SESSION}" 2>/dev/null || true
tmux new-session -d -s "${SESSION}" -n "I" -c /home/syd/projects/Scan64 "bash ${C}/scripts/terminal-I.sh; echo 'Terminal I exited. Press enter.'; read"
echo "Wave 3 launched: tmux attach -t ${SESSION}"
[ "${NO_ATTACH:-0}" = "1" ] || tmux attach-session -t "${SESSION}"
