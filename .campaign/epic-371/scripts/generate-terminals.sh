#!/usr/bin/env bash
# Generate terminal wrappers, progress files, and wave launchers for epic-371.
# Re-run after editing the TERMINALS table. Existing progress files are kept unless FORCE=1.
set -euo pipefail

C="/home/syd/projects/Scan64/.campaign/epic-371"
S="${C}/scripts"

# id|wave|title|branch|issues|writes|target
TERMINALS=(
"A|1|Board+Pieces|feat/358-359-fide-board-pieces|#359|boardAppearance.ts, pieces.css, Board.css, SettingsScreen*, board/piece assets, THIRD_PARTY_NOTICES.md, docs/user-guide.md board section|All C.02 schemes; 2+ new Staunton sets; FIDE defaults"
"B|1|Engine|feat/360-swappable-engine|#360|src/scan64/providers/stockfish/, src/scan64/providers/engine/ (new), src/scan64/chess/analysis/models.py, engine config, engine tests, benchmarks/engines/ (new), docs/system-design.md, docs/engines.md (new)|Second engine runs full analysis via config only"
"C|1|Grounding|feat/366-ground-claim-text|#366|src/scan64/explanations/validator.py, src/scan64/explanations/assembly.py (fallback logging only), explanation tests, chess-lesson-spec claims (only if required)|15+ adversarial rejected, 10+ correct pass; fallbacks logged"
"D|1|Narrator-doc|docs/361-narrator-pipeline|#361|docs/narrator-pipeline.md (new) only|Accurate pipeline doc with diagram and gaps"
"E|2|Materials|feat/363-material-boards|#363|boardAppearance.ts, Board.css, SettingsScreen*, board texture assets, scripts/build-board-textures.py (new), THIRD_PARTY_NOTICES.md, docs/user-guide.md board section|4+ wood, 2+ stone boards within budget"
"F|2|Narrator-facts|feat/367-narrator-facts|#367|src/scan64/explanations/, src/scan64/learning/evidence/, src/scan64/providers/llm/local_ollama.py, schemas/ (new prompt schema), explanation/evidence tests, docs/narrator-pipeline.md, docs/system-design.md narrator section|Schema-valid JSON prompt; measured local run"
"G|2|Narrator-bench|feat/369-narrator-benchmark|#369|benchmarks/narrator/ (new), docs/evaluation/narrator-models.md (new)|<=6 models x 50 positions x 3 runs; report + #369 comment"
"I|3|Board-narration|feat/368-analysis-board-narration|#368|src/scan64/api/ (new explanation endpoint), src/scan64/explanations/, AnalysisScreen*, web API client, tests, docs/user-guide.md analysis board, docs/narrator-pipeline.md|Grounded explanation of 3+ lines for any position"
)

declare -A WAVE_IDS WAVE_NAMES=([1]="Foundations" [2]="Content" [3]="Features")

for row in "${TERMINALS[@]}"; do
  IFS='|' read -r id wave title branch issues writes target <<<"${row}"
  WAVE_IDS[$wave]+="${id} "

  cat >"${S}/terminal-${id}.sh" <<EOF
#!/usr/bin/env bash
# Terminal ${id} — ${title} (wave ${wave}) — ${issues}
exec bash "${S}/run-terminal.sh" "${id}" "${branch}" "${issues}" "${writes}" "\${1:-fresh}"
EOF
  chmod +x "${S}/terminal-${id}.sh"

  pf="${C}/progress/terminal-${id}.md"
  if [ ! -f "${pf}" ] || [ "${FORCE:-0}" = "1" ]; then
    {
      echo "---"
      echo "terminal: ${id}"
      echo "title: \"${title}\""
      echo "campaign: epic-371"
      echo "wave: ${wave}"
      echo "status: pending"
      echo "branch: ${branch}"
      echo "writes_to: \"${writes}\""
      echo "issue_refs: \"${issues}\""
      echo "target: \"${target}\""
      echo "blocked_by: []"
      echo "started: null"
      echo "updated: null"
      echo "---"
      echo
      echo "# Terminal ${id} — ${title}"
      echo
      echo "## Issues"
      echo
      echo "| Issue | Task | Target | Status | Actual |"
      echo "|-------|------|--------|--------|--------|"
      for n in ${issues}; do
        t="$(gh issue view "${n#\#}" -R Mathews-Tom/Scan64 --json title --jq .title)"
        echo "| ${n} | ${t} | ${target} | pending | — |"
      done
      echo
      echo "## Results"
      echo
      echo "## Log"
    } >"${pf}"
  fi
done

for wave in 1 2 3; do
  ids=(${WAVE_IDS[$wave]})
  lw="${S}/launch-wave-${wave}.sh"
  {
    echo '#!/usr/bin/env bash'
    echo "# Launch Wave ${wave} — ${WAVE_NAMES[$wave]} (tmux). Set NO_ATTACH=1 to skip attaching."
    echo 'set -euo pipefail'
    echo "C=\"${C}\""
    echo "SESSION=\"campaign-epic-371-w${wave}\""
    echo 'mkdir -p "${C}/.markers"'
    for id in "${ids[@]}"; do echo "rm -f \"\${C}/.markers/terminal-${id}.done\""; done
    echo 'tmux kill-session -t "${SESSION}" 2>/dev/null || true'
    first=1
    for id in "${ids[@]}"; do
      cmd="bash \${C}/scripts/terminal-${id}.sh; echo 'Terminal ${id} exited. Press enter.'; read"
      if [ $first = 1 ]; then
        echo "tmux new-session -d -s \"\${SESSION}\" -n \"${id}\" -c /home/syd/projects/Scan64 \"${cmd}\""
        first=0
      else
        echo "tmux new-window -t \"\${SESSION}\" -n \"${id}\" -c /home/syd/projects/Scan64 \"${cmd}\""
      fi
    done
    echo "echo \"Wave ${wave} launched: tmux attach -t \${SESSION}\""
    echo '[ "${NO_ATTACH:-0}" = "1" ] || tmux attach-session -t "${SESSION}"'
  } >"${lw}"
  chmod +x "${lw}"
done

echo "Generated: $(ls "${S}" | tr '\n' ' ')"
