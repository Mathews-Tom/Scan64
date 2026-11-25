#!/usr/bin/env bash
# Launch Wave 1 — Foundations (tmux). Set NO_ATTACH=1 to skip attaching.
set -euo pipefail
C="/home/syd/projects/Scan64/.campaign/epic-371"
SESSION="campaign-epic-371-w1"
mkdir -p "${C}/.markers"
rm -f "${C}/.markers/terminal-A.done"
rm -f "${C}/.markers/terminal-B.done"
rm -f "${C}/.markers/terminal-C.done"
rm -f "${C}/.markers/terminal-D.done"
tmux kill-session -t "${SESSION}" 2>/dev/null || true
tmux new-session -d -s "${SESSION}" -n "A" -c /home/syd/projects/Scan64 "bash ${C}/scripts/terminal-A.sh; echo 'Terminal A exited. Press enter.'; read"
tmux new-window -t "${SESSION}" -n "B" -c /home/syd/projects/Scan64 "bash ${C}/scripts/terminal-B.sh; echo 'Terminal B exited. Press enter.'; read"
tmux new-window -t "${SESSION}" -n "C" -c /home/syd/projects/Scan64 "bash ${C}/scripts/terminal-C.sh; echo 'Terminal C exited. Press enter.'; read"
tmux new-window -t "${SESSION}" -n "D" -c /home/syd/projects/Scan64 "bash ${C}/scripts/terminal-D.sh; echo 'Terminal D exited. Press enter.'; read"
echo "Wave 1 launched: tmux attach -t ${SESSION}"
[ "${NO_ATTACH:-0}" = "1" ] || tmux attach-session -t "${SESSION}"
