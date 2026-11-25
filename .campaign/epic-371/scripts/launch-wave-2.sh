#!/usr/bin/env bash
# Launch Wave 2 — Content (tmux). Set NO_ATTACH=1 to skip attaching.
set -euo pipefail
C="/home/syd/projects/Scan64/.campaign/epic-371"
SESSION="campaign-epic-371-w2"
mkdir -p "${C}/.markers"
rm -f "${C}/.markers/terminal-E.done"
rm -f "${C}/.markers/terminal-F.done"
rm -f "${C}/.markers/terminal-G.done"
tmux kill-session -t "${SESSION}" 2>/dev/null || true
tmux new-session -d -s "${SESSION}" -n "E" -c /home/syd/projects/Scan64 "bash ${C}/scripts/terminal-E.sh; echo 'Terminal E exited. Press enter.'; read"
tmux new-window -t "${SESSION}" -n "F" -c /home/syd/projects/Scan64 "bash ${C}/scripts/terminal-F.sh; echo 'Terminal F exited. Press enter.'; read"
tmux new-window -t "${SESSION}" -n "G" -c /home/syd/projects/Scan64 "bash ${C}/scripts/terminal-G.sh; echo 'Terminal G exited. Press enter.'; read"
echo "Wave 2 launched: tmux attach -t ${SESSION}"
[ "${NO_ATTACH:-0}" = "1" ] || tmux attach-session -t "${SESSION}"
