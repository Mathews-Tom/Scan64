#!/usr/bin/env bash
# Launch Wave 3 (Windows / Git Bash): each terminal runs as a detached background process.
set -euo pipefail
C="D:/projects/Scan64/.campaign/epic-371"
mkdir -p "${C}/.markers" "${C}/logs" "${C}/.junk/markers"
for id in I; do
  m="${C}/.markers/terminal-${id}.done"
  [ -f "${m}" ] && mv "${m}" "${C}/.junk/markers/terminal-${id}.done.$(date +%Y%m%d_%H%M%S)"
  nohup bash "${C}/scripts/terminal-${id}.sh" >"${C}/logs/terminal-${id}.console.log" 2>&1 &
  echo "Terminal ${id} launched (pid $!)"
done
