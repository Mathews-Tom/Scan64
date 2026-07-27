# Execution Prompts — Scan64 (Learning Loop, M31–M44)

Prompts for the milestones in `.docs/DEVELOPMENT_PLAN.md`. M1–M30 behavior is established by merged Git history; their former planning prompts are historical local records.

## Global execution rules (apply to every goal)

- Use the `stacked-prs` skill: each implementation PR is based on the preceding stack branch until that base merges. The stack root targets `main`.
- Conventional Commits, atomic commits, multiple commits per PR grouped into a reviewable narrative, no attribution of any kind (no `Co-authored-by`, no tool mentions, no generated-by text, no emoji).
- Run the mandatory pre-implementation design gate before creating any product-code branch or changing any product code.
- The committed plan and prompt files are authoritative. `.docs/DEVELOPMENT_PLAN_HISTORY.md` is local, ignored evidence; rebuild it from committed artifacts, merged PRs, CI results, and current code when absent. Its absence is not plan loss and is not a `NO-GO`.
- A material plan change must update the current milestone and every affected future milestone in both authoritative files before implementation. Rebuild the dependency graph, critical path, and release-train membership after the update.
- A material revision requires a docs-only reconciliation PR: reviewed, green, and externally merged before any code PR. It must never be folded into an implementation PR.
- A shared mismatch affecting a proposed parallel wave blocks product-code work in every affected lane. Do not continue scaffolding, partial implementation, or isolated ledger writes while reconciliation is pending.
- Hosted CI is authoritative. `.github/workflows/ci.yml` runs `scripts/check.sh` with pinned Stockfish 18 on every pull request. Local verification is necessary but not sufficient; every PR needs a green hosted `Quality` check.
- `main` carries a branch ruleset (`pull_request`, `non_fast_forward`, `deletion`). Never push to `main` directly.
- The workflow's `pull_request` trigger has no `types:` list, so editing a PR's base does not retrigger CI. When merging a stack root to leaf, force a fresh run on each retargeted child rather than trusting a displayed check.
- Never trigger an external or bot reviewer. The repository's configured reviewer runs on its own.
- `GO` only makes the milestone stack merge-eligible. Release preparation stays deferred until every milestone in its train is externally merged.
- No milestone updates a version or changelog artifact. See `DEVELOPMENT_PLAN.md` §2 `> GAP:` on release policy.

---

### M31 — Persist analysis artifacts with player attribution

```text
/goal Deliver milestone M31 (Persist analysis artifacts with player attribution) from DEVELOPMENT_PLAN.md as a reviewed stack of PRs.

CONTEXT: DEVELOPMENT_PLAN.md §6 Section G M31 + source .docs/2026-07-26-learning-loop-enhancement-plan.md §2.1 (G1-G3) and .docs/scan64-system-design.md §7 (learning pipeline), §17 (data model). Preconditions: none - M31 is the root of this plan. Repo: Python 3.12+/uv, FastAPI + SQLModel + SQLite, pytest/ruff/mypy --strict, single gate scripts/check.sh, hosted CI job "Quality".
OBJECTIVE: A completed analysis job leaves durable, player-attributed Position, EngineAnalysis, and Evidence rows so the read endpoints that already ship return real data. Acceptance: after a job on an imported game owned by player P, GET /v1/games/{id}/positions returns one row per persisted position with its engine analysis attached; GET /v1/players/P/evidence returns the evidence backing every persisted lesson; a search for the literal player_id="system" in src/ returns no match; no code path constructs an Evidence object it does not persist.
RECONCILED OWNERSHIP (H-002) AND MIGRATION (H-003) CONTRACT: Alembic upgrades legacy SQLite data before M31 columns are queried. `POST /v1/games` requires an existing `player_id` and writes nullable-migrated `Game.owner_player_id`; the PGN import client supplies the active player identity. During migration only, a legacy game with a linked `PlaySession` is backfilled from that session's player; unlinked legacy games remain ownerless. M31 derives ongoing analysis, persisted-lesson, and evidence attribution from `Game.owner_player_id`; do not use `PlaySession` as an imported-game ownership surrogate.
RELEASE TRAIN: target=unversioned - "learning loop closed"; included milestones=M31-M37; preparation trigger=all included milestones externally merged; required artifacts=none (DEVELOPMENT_PLAN.md §2 GAP on release policy); release verification=scripts/check.sh exits 0 on main after the final merge plus M37's manual loop walkthrough; publication=not requested.

PRE-IMPLEMENTATION DESIGN GATE:
1. Read DEVELOPMENT_PLAN.md §6 M31, its §1 source-map rows, this prompt, and .docs/DEVELOPMENT_PLAN_HISTORY.md.
2. Inspect src/scan64/chess/analysis/jobs.py, src/scan64/api/reports.py:72-113, src/scan64/api/games.py:200-222, and the SQLModel definitions for Position, EngineAnalysis, and Evidence. Confirm G1-G3 still hold and that no interim change already persists these rows.
2a. Inspect `Game`, `GameCreate`, the PGN import caller, and the schema bootstrap. Confirm imports currently have no durable owner and `create_all()` cannot migrate an existing table; reconcile with the owner contract and Alembic upgrade path above, not a synthetic session or an ad-hoc schema fallback.
3. Revalidate objective, interfaces, dependencies, acceptance, verification, risks, release train, and every dependent milestone listed in M31's Design reevaluation row (M32, M33, M34, M40, M41, M42, and transitively M35, M36, M37, M38, M39, M43, M44).
4. Append one ledger entry to .docs/DEVELOPMENT_PLAN_HISTORY.md: timestamp, milestone, decision, trigger, evidence, plan/prompt sections changed, downstream impact, implementation authorization.
5. If no material mismatch exists, report `DESIGN GO - PLAN REVISION: none`; this authorizes implementation.
6. If a mismatch exists, update both authoritative artifacts for M31 and every affected future milestone, append the revision ID, and report `DESIGN GO - PLAN REVISION: <entry IDs>`. This records a completed diagnosis but blocks product code until the reconciliation prerequisite merges.
7. If validity cannot be established, report `DESIGN NO-GO - REASON: <evidence>` and stop. After a reconciliation PR merges, repeat this gate and require `DESIGN GO - PLAN REVISION: none` before implementation.

RECONCILIATION RULE: A material revision opens `docs(plan): reconcile M31 design` as a docs-only prerequisite PR. It contains no product code, must be reviewed, green, and externally merged before any code PR, and must not be folded into an implementation PR.

PLANNED STACK (refine only to keep PRs reviewable):
0. Conditional prerequisite `docs(plan): reconcile M31 design` - scope: authoritative plan/prompt updates only; gate: reviewed, green, merged before the implementation stack.
1. PR-1 Migration foundation - scope: bootstrap Alembic, establish the baseline, and prove a populated legacy SQLite fixture upgrades without loss before M31 columns are queried; commits: "build(db): bootstrap schema migrations"; verification: `uv run pytest tests/integration/test_schema_migrations.py`
2. PR-2 Durable game ownership and analysis attribution (on PR-1) - scope: require player-owned imports; migrate `Game.owner_player_id` and `PersistedLessonOpportunity.player_id`; backfill a legacy game's owner once only when its existing `PlaySession` supplies one while leaving session-less rows ownerless; extend `tests/integration/test_schema_migrations.py` for both branches; update the PGN import identity handoff and component coverage; derive analysis context from the game's owner; update the evidence read query; and delete the `"system"` literal; commits: "feat(games): attribute imported games to their owner", "feat(web): submit the active player with a PGN import", "feat(analysis): resolve the owning player for an analysis job", "feat(analysis): attribute persisted lesson opportunities to a player"; verification: `uv run pytest tests/integration/test_schema_migrations.py && uv run pytest tests/unit -k analysis && uv run mypy --strict src/ && pnpm --dir apps/scan64-web test`
3. PR-3 Persist positions and engine analyses (on PR-2) - scope: write Position and EngineAnalysis rows for fast-pass candidates and focused-pass positions inside the job's session; commits: "feat(analysis): persist analysed positions", "feat(analysis): persist engine analysis for candidate positions"; verification: `uv run pytest tests/integration/test_analysis_persistence.py -k positions`
4. PR-4 Persist evidence (on PR-3) - scope: add the constructed Evidence objects to the session instead of discarding them, linked to their persisted position; commits: "fix(analysis): persist the evidence backing each diagnosis"; verification: `uv run pytest tests/integration/test_analysis_persistence.py -k evidence`
5. PR-5 End-to-end persistence coverage (on PR-4) - scope: tests/integration/test_analysis_persistence.py asserting both read endpoints return real data after a job, plus a guard test that fails when an Evidence object is constructed without persistence; commits: "test(analysis): cover the persisted analysis read surface"; verification: `uv run pytest tests/integration/test_analysis_persistence.py`

CONSTRAINTS: no scope leakage, minimal dependencies, repo style, no version/changelog updates. Persist analysis only for candidate and focused-pass positions - do not write a row per ply. Do not reassign an owner resolved by migration or assign an owner to a session-less legacy game; new detectors, profile updates, and auto-triggering are out of scope, M33, M34, and M32 respectively. The source's PostgreSQL migration-CI target remains a recorded gap; do not claim it from SQLite-only verification.
VERIFICATION (must pass): `uv run pytest tests/integration/test_schema_migrations.py tests/integration/test_analysis_persistence.py` passes; `pnpm --dir apps/scan64-web test` passes; `scripts/check.sh` exits 0; manual - import a PGN as the active player, poll the job to completed, assert GET /v1/games/{id}/positions and GET /v1/players/{id}/evidence are both non-empty.
REVIEW:
Per PR:
- Scope matches its purpose; contracts match the reconciled plan; behavior is meaningfully tested.
- Failures are loud; security, data safety, and rollback requirements are addressed where relevant.
- History is atomic, conventional, attribution-free, and free of unrelated formatting churn.
- PR-specific verification output is captured.
Whole stack:
- Bases form one valid stack; cumulative acceptance and integration hold; hosted CI is green on every PR; no regression coverage is removed without replacement.
- The docs-only root, when present, is reviewed and green before dependent code PRs.
- Report PR URLs, bases, verification, risks, manual gates, and review completion.
FINAL VERDICTS:
- Report the design verdict before the merge verdict.
- Then report exactly one merge verdict: `GO - RELEASE: unversioned - learning loop closed - RELEASE PREP: pending` or `NO-GO - RELEASE: unversioned - learning loop closed - REASON: <blocking gate>`.
- `GO` requires `DESIGN GO`, every PR correctly based/reviewed/green, local verification, and full milestone acceptance. `NO-GO` applies to pending or failed checks, incomplete review, scope drift, ambiguous readiness, manual gates, or unresolved release target.
DONE: design verdict with evidence; when authorized, a reviewed stack with a release-aware merge verdict and evidence.
```

---

### M32 — Analyse the games you actually play

```text
/goal Deliver milestone M32 (Analyse the games you actually play) from DEVELOPMENT_PLAN.md as a reviewed stack of PRs.

CONTEXT: DEVELOPMENT_PLAN.md §6 Section G M32 + source .docs/2026-07-26-learning-loop-enhancement-plan.md §2.1 (G4, G9) and .docs/scan64-system-design.md §7. Preconditions: M31 merged. Repo: Python 3.12+/uv, FastAPI + SQLModel + SQLite, pytest/ruff/mypy --strict, scripts/check.sh, hosted CI job "Quality".
M31 OWNERSHIP CONTRACT (H-002): played games must set the same durable `Game.owner_player_id` used by imported games; M32's player-games endpoint must query this owner without duplicate session joins.
OBJECTIVE: Reaching a terminal state in a play session produces the same diagnostic output as importing that game as a PGN, attributed to the player who played it. Acceptance: playing to mate or resignation produces a completed AnalysisJob with no further client call; Game.pgn for a played game is a valid PGN that re-imports cleanly and names the player; GET /v1/players/{id}/games lists both played and imported games; a player exceeding the in-flight cap has work queued, never dropped.
RELEASE TRAIN: target=unversioned - "learning loop closed"; included milestones=M31-M37; preparation trigger=all included milestones externally merged; required artifacts=none (DEVELOPMENT_PLAN.md §2 GAP on release policy); release verification=scripts/check.sh exits 0 on main after the final merge plus M37's manual loop walkthrough; publication=not requested.

PRE-IMPLEMENTATION DESIGN GATE:
1. Read DEVELOPMENT_PLAN.md §6 M32, its §1 source-map rows, this prompt, and .docs/DEVELOPMENT_PLAN_HISTORY.md when present.
2. Inspect src/scan64/chess/games/play_session_service.py:65-149, src/scan64/api/play.py, and M31's merged persistence path plus its CI evidence and verification output.
3. Revalidate objective, interfaces, dependencies, acceptance, verification, risks, release train, and every dependent milestone listed in M32's Design reevaluation row (M34, M39, and transitively M36, M37, M38, M43, M44).
4. Append one ledger entry to .docs/DEVELOPMENT_PLAN_HISTORY.md: timestamp, milestone, decision, trigger, evidence, plan/prompt sections changed, downstream impact, implementation authorization.
5. If no material mismatch exists, report `DESIGN GO - PLAN REVISION: none`; this authorizes implementation.
6. If a mismatch exists, update both authoritative artifacts for M32 and every affected future milestone, append the revision ID, and report `DESIGN GO - PLAN REVISION: <entry IDs>`. This blocks product code until the reconciliation prerequisite merges.
7. If validity cannot be established, report `DESIGN NO-GO - REASON: <evidence>` and stop. After a reconciliation PR merges, repeat this gate and require `DESIGN GO - PLAN REVISION: none`.

RECONCILIATION RULE: A material revision opens `docs(plan): reconcile M32 design` as a docs-only prerequisite PR. It contains no product code, must be reviewed, green, and externally merged before any code PR, and must not be folded into an implementation PR.

PLANNED STACK (refine only to keep PRs reviewable):
0. Conditional prerequisite `docs(plan): reconcile M32 design` - scope: authoritative plan/prompt updates only; gate: reviewed, green, merged before the implementation stack.
1. PR-1 Real player identity and ownership on played games - scope: `PlaySessionService` writes `Game.owner_player_id`, `white`, and `black` from the session's player and opponent config instead of the `"Player"`/`"Opponent"` literals; commits: "feat(play): attribute played games to their owner", "fix(play): record the real players on a game created from a session"; verification: `uv run pytest tests/unit -k play_session`
2. PR-2 PGN synchronisation (on PR-1) - scope: maintain a valid Game.pgn for played games; commits: "feat(play): keep a valid PGN for every played game"; verification: `uv run pytest tests/integration -k pgn_roundtrip`
3. PR-3 Resign and terminal-state transition (on PR-2) - scope: POST /v1/play-sessions/{id}/resign and a single terminal-state transition point; commits: "feat(api): allow a player to resign a session", "refactor(play): funnel terminal-state handling through one transition"; verification: `uv run pytest tests/integration -k resign`
4. PR-4 Auto-enqueue analysis with an in-flight cap (on PR-3) - scope: enqueue an analysis job on terminal state, per-player in-flight cap that queues rather than drops; commits: "feat(play): analyse a game when its session ends", "feat(play): bound concurrent analysis per player"; verification: `uv run pytest tests/integration/test_play_session_analysis.py`
5. PR-5 Player games listing (on PR-4) - scope: GET /v1/players/{id}/games covering played and imported games; commits: "feat(api): list a player's games"; verification: `uv run pytest tests/integration -k player_games`

CONSTRAINTS: no scope leakage, minimal dependencies, repo style, no version/changelog updates. The in-flight cap is interim and is superseded by M41's admission control - do not build a second quota system. Do not surface any of this in the UI; that is M39.
VERIFICATION (must pass): `uv run pytest tests/integration/test_play_session_analysis.py` passes; `scripts/check.sh` exits 0; manual - play and resign a session against Stockfish, then read the player's games and evidence.
REVIEW:
Per PR:
- Scope matches its purpose; contracts match the reconciled plan; behavior is meaningfully tested.
- Failures are loud; security, data safety, and rollback requirements are addressed where relevant.
- History is atomic, conventional, attribution-free, and free of unrelated formatting churn.
- PR-specific verification output is captured.
Whole stack:
- Bases form one valid stack; cumulative acceptance and integration hold; hosted CI is green on every PR; no regression coverage is removed without replacement.
- The docs-only root, when present, is reviewed and green before dependent code PRs.
- Report PR URLs, bases, verification, risks, manual gates, and review completion.
FINAL VERDICTS:
- Report the design verdict before the merge verdict.
- Then report exactly one merge verdict: `GO - RELEASE: unversioned - learning loop closed - RELEASE PREP: pending` or `NO-GO - RELEASE: unversioned - learning loop closed - REASON: <blocking gate>`.
- `GO` requires `DESIGN GO`, every PR correctly based/reviewed/green, local verification, and full milestone acceptance.
DONE: design verdict with evidence; when authorized, a reviewed stack with a release-aware merge verdict and evidence.
```

---

### M33 — Wire the full detector set and the focused pass

```text
/goal Deliver milestone M33 (Wire the full detector set and the focused pass) from DEVELOPMENT_PLAN.md as a reviewed stack of PRs.

CONTEXT: DEVELOPMENT_PLAN.md §6 Section G M33 + source .docs/2026-07-26-learning-loop-enhancement-plan.md §2.1 (G6, G7) and .docs/scan64-system-design.md §7 (pipeline), §8 (taxonomy). Preconditions: M31 merged. Repo: Python 3.12+/uv, FastAPI + SQLModel, pinned Stockfish 18 in CI, pytest/ruff/mypy --strict, scripts/check.sh.
M31 OWNERSHIP CONTRACT (H-002): production detector assembly receives player context from `Game.owner_player_id`; do not reintroduce synthetic identities or infer ownership from `PlaySession`.
OBJECTIVE: All ten seeded taxonomy codes can be diagnosed through the production job path and flagged positions retain deep MultiPV evidence. Acceptance: a legal production-path fixture corpus runs through `run_analysis_for_game`, yields at least one diagnosis for every seeded code, and never supplies benchmark `mock_evidence`; a position where two detectors fire produces exactly one primary diagnosis with the loser retained in `Diagnosis.secondary`; `FocusedPassOrchestrator` is reached from that path; the coverage report emits per-code TP, FP, FN, and precision across primary and secondary outputs for registry and isolated-detector runs over the same emitted evidence, with no regression.
RELEASE TRAIN: target=unversioned - "learning loop closed"; included milestones=M31-M37; preparation trigger=all included milestones externally merged; required artifacts=none (DEVELOPMENT_PLAN.md §2 GAP on release policy); release verification=scripts/check.sh exits 0 on main after the final merge plus M37's manual loop walkthrough; publication=not requested.

PRE-IMPLEMENTATION DESIGN GATE:
1. Read DEVELOPMENT_PLAN.md §6 M33, its §1 source-map rows, this prompt, and .docs/DEVELOPMENT_PLAN_HISTORY.md when present.
2. Inspect src/scan64/chess/analysis/jobs.py:54-92, src/scan64/chess/analysis/orchestration.py:101-117, src/scan64/learning/plugins/registry.py:38-64, src/scan64/benchmarks/diagnosis_report.py:5-54, and M31's merged persistence path with its CI evidence.
3. Revalidate objective, interfaces, dependencies, acceptance, verification, risks, release train, and every dependent milestone listed in M33's Design reevaluation row (M34, M35, and transitively M36, M37, M38, M43).
4. Append one ledger entry to .docs/DEVELOPMENT_PLAN_HISTORY.md: timestamp, milestone, decision, trigger, evidence, plan/prompt sections changed, downstream impact, implementation authorization.
5. If no material mismatch exists, report `DESIGN GO - PLAN REVISION: none`; this authorizes implementation.
6. If a mismatch exists, update both authoritative artifacts for M33 and every affected future milestone, append the revision ID, and report `DESIGN GO - PLAN REVISION: <entry IDs>`. This blocks product code until the reconciliation prerequisite merges.
7. If validity cannot be established, report `DESIGN NO-GO - REASON: <evidence>` and stop. After a reconciliation PR merges, repeat this gate and require `DESIGN GO - PLAN REVISION: none`.

RECONCILIATION RULE: A material revision opens `docs(plan): reconcile M33 design` as a docs-only prerequisite PR. It contains no product code, must be reviewed, green, and externally merged before any code PR, and must not be folded into an implementation PR.

PLANNED STACK (refine only to keep PRs reviewable):
0. Conditional prerequisite `docs(plan): reconcile M33 design` - scope: authoritative plan/prompt updates only; gate: reviewed, green, merged before the implementation stack.
1. PR-1 Registry bootstrap - scope: register every seeded detector at app startup and bind registry lifecycle to the FastAPI lifespan; commits: "feat(learning): register seeded detectors at startup"; verification: `uv run pytest tests/unit/test_plugin_interfaces.py`
2. PR-2 Resolve detectors through the registry (on PR-1) - scope: replace the concrete HangingPieceDetector construction in jobs.py with a registry lookup, preserving current behaviour with one registered detector; commits: "refactor(analysis): resolve detectors through the plugin registry"; verification: `uv run pytest tests/integration -k detector_resolution`
3. PR-3 Focused pass and production evidence composition (on PR-2) - scope: run `FocusedPassOrchestrator` on fast-pass candidates; persist its MultiPV output; derive the existing detector evidence kinds and code-specific payloads only from the candidate's legal game history, board states, and engine provenance; commits: "feat(analysis): run the focused pass on candidate positions"; verification: `uv run pytest tests/integration/test_stockfish_pipeline.py`
4. PR-4 Arbitration (on PR-3) - scope: deterministic primary/secondary selection when several detectors fire on one position, retaining the winner in `Diagnosis.primary` and every eligible loser in deterministic `Diagnosis.secondary` order; gate each candidate on its taxonomy-declared minimum engine evidence; commits: "feat(analysis): arbitrate competing diagnoses on one position"; verification: `uv run pytest tests/unit -k arbitration`
5. PR-5 Production-path coverage and precision report (on PR-4) - scope: a legal fixture corpus with games, candidate responses, focused MultiPV responses, expected labels, and overlap cases; `tests/integration/test_live_detector_coverage.py` drives it through `run_analysis_for_game`, reports registry-versus-isolated per-code precision, and never imports the benchmark harness's `mock_evidence`; commits: "test(analysis): cover every seeded code through the live job path"; verification: `uv run pytest tests/integration/test_live_detector_coverage.py`

CONSTRAINTS: no scope leakage, minimal dependencies, repo style, no version/changelog updates. Introduce no new detector classes and do not change the taxonomy. Production evidence must trace to `Game` move history, board states, and fast/focused engine output; fixture-only evidence must never cross that boundary. Do not update the profile; that is M34.
VERIFICATION (must pass): `uv run pytest tests/integration/test_live_detector_coverage.py tests/integration/test_stockfish_pipeline.py` passes with the per-code production-fixture precision report captured; `scripts/check.sh` exits 0.
REVIEW:
Per PR:
- Scope matches its purpose; contracts match the reconciled plan; behavior is meaningfully tested.
- Failures are loud; false-positive risk from newly enabled detectors is quantified, not asserted.
- History is atomic, conventional, attribution-free, and free of unrelated formatting churn.
- PR-specific verification output is captured.
Whole stack:
- Bases form one valid stack; cumulative acceptance and integration hold; hosted CI is green on every PR; no regression coverage is removed without replacement.
- The docs-only root, when present, is reviewed and green before dependent code PRs.
- Report PR URLs, bases, verification, risks, manual gates, and review completion.
FINAL VERDICTS:
- Report the design verdict before the merge verdict.
- Then report exactly one merge verdict: `GO - RELEASE: unversioned - learning loop closed - RELEASE PREP: pending` or `NO-GO - RELEASE: unversioned - learning loop closed - REASON: <blocking gate>`.
- `GO` requires `DESIGN GO`, every PR correctly based/reviewed/green, local verification, and full milestone acceptance.
DONE: design verdict with evidence; when authorized, a reviewed stack with a release-aware merge verdict and evidence.
```

---

### M34 — Diagnoses move the profile

```text
/goal Deliver milestone M34 (Diagnoses move the profile) from DEVELOPMENT_PLAN.md as a reviewed stack of PRs.

CONTEXT: DEVELOPMENT_PLAN.md §6 Section G M34 + source .docs/2026-07-26-learning-loop-enhancement-plan.md §2.1 (G5), §2.2 (G14, G15) and §2.4 (G31), .docs/scan64-system-design.md §9 (player model), §21 (scheduler), §8.10 (taxonomy governance). Preconditions: M32 and M33 merged. Repo: Python 3.12+/uv, FastAPI + SQLModel, Hypothesis for property tests, pytest/ruff/mypy --strict, scripts/check.sh.
M31 OWNERSHIP CONTRACT (H-002): profile observation consumes the persisted opportunity's player id, which derives from `Game.owner_player_id`, and its non-null game id; reject missing owner or game rather than silently assigning one.
OBJECTIVE: A diagnosed weakness changes the player's Bayesian skill state and schedules review, and a taxonomy rename can safely remap the live rows this creates. Acceptance: two games containing the same diagnosis lower that concept's expected mastery monotonically and narrow its uncertainty; a player rated 1200 and a player rated 1900 start a new concept at different priors, neither at (1.0, 1.0); every generated lesson has a ReviewSchedule row with a due date and its diagnosed skill_id; re-analysing the same owned game does not change mastery a second time because ProfileObservation persists the non-null key (player, game, position, skill); a skill_id rename remaps live SkillState.concept_code and ReviewSchedule.skill_id rows, merging a target-key collision deterministically; an unmappable code remains retired with a recorded reason, never silently dropped.
RELEASE TRAIN: target=unversioned - "learning loop closed"; included milestones=M31-M37; preparation trigger=all included milestones externally merged; required artifacts=none (DEVELOPMENT_PLAN.md §2 GAP on release policy); release verification=scripts/check.sh exits 0 on main after the final merge plus M37's manual loop walkthrough; publication=not requested.

PRE-IMPLEMENTATION DESIGN GATE:
1. Read DEVELOPMENT_PLAN.md §6 M34, its §1 source-map rows, this prompt, and .docs/DEVELOPMENT_PLAN_HISTORY.md when present.
2. Inspect src/scan64/content/tracking.py:7-40, src/scan64/learning/profiling/priors.py:3-20, src/scan64/api/learning.py:199-230, src/scan64/learning/diagnosis/taxonomy/migration.py:11-51, the SkillState and ReviewSchedule schemas, and the merged M32/M33 paths with their CI evidence and verification output.
3. Revalidate objective, interfaces, dependencies, acceptance, verification, risks, release train, and every dependent milestone listed in M34's Design reevaluation row (M36, M37, M38, M42, and transitively M43).
4. Append one ledger entry to .docs/DEVELOPMENT_PLAN_HISTORY.md: timestamp, milestone, decision, trigger, evidence, plan/prompt sections changed, downstream impact, implementation authorization.
5. If no material mismatch exists, report `DESIGN GO - PLAN REVISION: none`; this authorizes implementation.
6. If a mismatch exists, update both authoritative artifacts for M34 and every affected future milestone, append the revision ID, and report `DESIGN GO - PLAN REVISION: <entry IDs>`. This blocks product code until the reconciliation prerequisite merges.
7. If validity cannot be established, report `DESIGN NO-GO - REASON: <evidence>` and stop. After a reconciliation PR merges, repeat this gate and require `DESIGN GO - PLAN REVISION: none`.

RECONCILIATION RULE: A material revision opens `docs(plan): reconcile M34 design` as a docs-only prerequisite PR. It contains no product code, must be reviewed, green, and externally merged before any code PR, and must not be folded into an implementation PR.

PLANNED STACK (refine only to keep PRs reviewable):
0. Conditional prerequisite `docs(plan): reconcile M34 design` - scope: authoritative plan/prompt updates only; gate: reviewed, green, merged before the implementation stack.
1. PR-1 Characterise the existing update path - scope: characterisation tests pinning current famous-game attempt behaviour before refactoring; commits: "test(content): pin current skill-state update behaviour"; verification: `uv run pytest tests/unit -k tracking`
2. PR-2 Unified profile-update service (on PR-1) - scope: one service used by both the analysis path and the content-attempt path, replacing the duplicated logic in content/tracking.py; commits: "refactor(learning): extract a single profile-update service"; verification: `uv run pytest tests/unit -k profile_update`
3. PR-3 Rating priors (on PR-2) - scope: apply rating-band priors on first observation of a concept via learning/profiling/priors.py; commits: "feat(learning): seed new concepts from rating-band priors"; verification: `uv run pytest tests/unit -k priors`
4. PR-4 Analysis-driven observations with idempotency (on PR-3) - scope: add the Alembic revision for ProfileObservation, ReviewSchedule.skill_id, and retained retirement metadata with non-retired defaults; persist ProfileObservation keyed by non-null (player, game, position, skill); apply diagnosis findings as negative observations only when its insert succeeds; reject missing owner or game; commits: "feat(db): persist profile observations and taxonomy state", "feat(learning): apply analysis findings to the player profile", "feat(learning): make repeat analysis idempotent"; verification: `uv run pytest tests/integration/test_schema_migrations.py tests/integration/test_profile_updates_from_analysis.py`
5. PR-5 Review scheduling and monotonicity property (on PR-4) - scope: create a ReviewSchedule with the diagnosis skill_id when a lesson is generated and update it on attempt; tests/property/test_mastery_monotonicity.py; commits: "feat(learning): schedule review for every generated lesson", "test(learning): prove repeated diagnoses lower mastery monotonically"; verification: `uv run pytest tests/property/test_mastery_monotonicity.py`
6. PR-6 Live taxonomy migration hook (on PR-5) - scope: invoke learning/diagnosis/taxonomy/migration.py over live SkillState.concept_code and ReviewSchedule.skill_id rows at startup with a no-op default table; mark unmappable rows retired with a reason; when a renamed SkillState collides with its target, preserve the target prior, add the old evidence above its own prior, retain the later timestamp, then retire the old row; assert every migration path is idempotent; commits: "feat(learning): remap live skill state through the taxonomy migration table"; verification: `uv run pytest tests/integration/test_taxonomy_migration_live.py`

CONSTRAINTS: no scope leakage, minimal dependencies, repo style, no version/changelog updates. Do not change what the reports return; that is M36. Do not change session composition; that is M38. The migration hook defaults to a no-op table - never mutate live rows without an explicit mapping. Retiring a code preserves its SkillState and ReviewSchedule rows with recorded metadata; no migration deletes or silently recreates a row.
VERIFICATION (must pass): `uv run pytest tests/integration/test_schema_migrations.py tests/integration/test_profile_updates_from_analysis.py tests/property/test_mastery_monotonicity.py tests/integration/test_taxonomy_migration_live.py` passes; `scripts/check.sh` exits 0.
REVIEW:
Per PR:
- Scope matches its purpose; contracts match the reconciled plan; behavior is meaningfully tested.
- The idempotency key is asserted by a test, not assumed; the refactor preserves characterised behaviour.
- History is atomic, conventional, attribution-free, and free of unrelated formatting churn.
- PR-specific verification output is captured.
Whole stack:
- Bases form one valid stack; cumulative acceptance and integration hold; hosted CI is green on every PR; no regression coverage is removed without replacement.
- The docs-only root, when present, is reviewed and green before dependent code PRs.
- Report PR URLs, bases, verification, risks, manual gates, and review completion.
FINAL VERDICTS:
- Report the design verdict before the merge verdict.
- Then report exactly one merge verdict: `GO - RELEASE: unversioned - learning loop closed - RELEASE PREP: pending` or `NO-GO - RELEASE: unversioned - learning loop closed - REASON: <blocking gate>`.
- `GO` requires `DESIGN GO`, every PR correctly based/reviewed/green, local verification, and full milestone acceptance.
DONE: design verdict with evidence; when authorized, a reviewed stack with a release-aware merge verdict and evidence.
```

---

### M35 — Explanations that say something true about the position

```text
/goal Deliver milestone M35 (Explanations that say something true about the position) from DEVELOPMENT_PLAN.md as a reviewed stack of PRs.

CONTEXT: DEVELOPMENT_PLAN.md §6 Section G M35 + source .docs/2026-07-26-learning-loop-enhancement-plan.md §2.1 (G8) and .docs/scan64-system-design.md §14 (LLM integration), §11.2 (LessonSpec explanation). Preconditions: M33 merged with its production evidence composer and code-specific evidence payload contract. Repo: Python 3.12+/uv, pytest/ruff/mypy --strict, scripts/check_licenses.py, scripts/check.sh.
OBJECTIVE: Every diagnosis the system can produce has an evidence-grounded explanation and no user-visible lesson falls back to a generic sentence. Acceptance: for each of the ten seeded codes the explanation names the specific square, piece, move, or line supplied by M33's provenance-bearing evidence payload; a taxonomy code or required payload field without a template fails the conformance test rather than rendering a fallback; with the LLM path enabled an ungrounded generation is rejected and the template output is used; the default install acquires no model dependency.
RELEASE TRAIN: target=unversioned - "learning loop closed"; included milestones=M31-M37; preparation trigger=all included milestones externally merged; required artifacts=none (DEVELOPMENT_PLAN.md §2 GAP on release policy); release verification=scripts/check.sh exits 0 on main after the final merge plus M37's manual loop walkthrough; publication=not requested.

PRE-IMPLEMENTATION DESIGN GATE:
1. Read DEVELOPMENT_PLAN.md §6 M35, its §1 source-map rows, this prompt, and .docs/DEVELOPMENT_PLAN_HISTORY.md when present.
2. Inspect src/scan64/explanations/templates/provider.py:3-25, src/scan64/explanations/validator.py:55-65, src/scan64/providers/llm/config.py:79-106, and M33's merged production evidence composer, code-specific payload contract, and fixture corpus with CI evidence.
3. Revalidate objective, interfaces, dependencies, acceptance, verification, risks, release train, and the dependent milestone listed in M35's Design reevaluation row (M37).
4. Append one ledger entry to .docs/DEVELOPMENT_PLAN_HISTORY.md: timestamp, milestone, decision, trigger, evidence, plan/prompt sections changed, downstream impact, implementation authorization.
5. If no material mismatch exists, report `DESIGN GO - PLAN REVISION: none`; this authorizes implementation.
6. If a mismatch exists, update both authoritative artifacts for M35 and every affected future milestone, append the revision ID, and report `DESIGN GO - PLAN REVISION: <entry IDs>`. This blocks product code until the reconciliation prerequisite merges.
7. If validity cannot be established, report `DESIGN NO-GO - REASON: <evidence>` and stop. After a reconciliation PR merges, repeat this gate and require `DESIGN GO - PLAN REVISION: none`.

RECONCILIATION RULE: A material revision opens `docs(plan): reconcile M35 design` as a docs-only prerequisite PR. It contains no product code, must be reviewed, green, and externally merged before any code PR, and must not be folded into an implementation PR.

PLANNED STACK (refine only to keep PRs reviewable):
0. Conditional prerequisite `docs(plan): reconcile M35 design` - scope: authoritative plan/prompt updates only; gate: reviewed, green, merged before the implementation stack.
1. PR-1 Coverage conformance test - scope: tests/conformance/test_explanation_coverage.py enumerating the taxonomy and M33's required code-specific payload fields, failing when any code lacks a template or required value; lands red-to-green with the fallback still present so the gap is demonstrated; commits: "test(explanations): require a template for every taxonomy code"; verification: `uv run pytest tests/conformance/test_explanation_coverage.py`
2. PR-2 Per-code evidence-grounded templates (on PR-1) - scope: a template per seeded code interpolating only M33 provenance-bearing evidence fields; remove the catch-all fallback; commits: "feat(explanations): add evidence-grounded templates for every seeded code", "fix(explanations): remove the generic fallback string"; verification: `uv run pytest tests/conformance/test_explanation_coverage.py`
3. PR-3 Config-gated LLM path (on PR-2) - scope: wire providers/llm behind explicit configuration, default off, with no model client in the default install; commits: "feat(explanations): allow an optional configured language-model provider"; verification: `uv run python scripts/check_licenses.py && uv run pytest tests/unit -k llm_config`
4. PR-4 Mandatory grounding validation (on PR-3) - scope: route generated text through explanations/validator.py and fall back to the template on rejection; commits: "feat(explanations): validate generated text against evidence before display"; verification: `uv run pytest tests/unit/test_grounded_explanation.py`

CONSTRAINTS: no scope leakage, minimal dependencies, repo style, no version/changelog updates. The default install must not acquire a model dependency. Do not add taxonomy codes. Rejection by the grounding validator is expected behaviour, not an error path to suppress.
VERIFICATION (must pass): `uv run pytest tests/conformance/test_explanation_coverage.py tests/unit/test_grounded_explanation.py` passes; `uv run python scripts/check_licenses.py` passes; `scripts/check.sh` exits 0.
REVIEW:
Per PR:
- Scope matches its purpose; contracts match the reconciled plan; behavior is meaningfully tested.
- Failures are loud; a missing template fails a test rather than degrading silently.
- History is atomic, conventional, attribution-free, and free of unrelated formatting churn.
- PR-specific verification output is captured.
Whole stack:
- Bases form one valid stack; cumulative acceptance and integration hold; hosted CI is green on every PR; no regression coverage is removed without replacement.
- The docs-only root, when present, is reviewed and green before dependent code PRs.
- Report PR URLs, bases, verification, risks, manual gates, and review completion.
FINAL VERDICTS:
- Report the design verdict before the merge verdict.
- Then report exactly one merge verdict: `GO - RELEASE: unversioned - learning loop closed - RELEASE PREP: pending` or `NO-GO - RELEASE: unversioned - learning loop closed - REASON: <blocking gate>`.
- `GO` requires `DESIGN GO`, every PR correctly based/reviewed/green, local verification, and full milestone acceptance.
DONE: design verdict with evidence; when authorized, a reviewed stack with a release-aware merge verdict and evidence.
```

---

### M36 — Reports computed from real data

```text
/goal Deliver milestone M36 (Reports computed from real data) from DEVELOPMENT_PLAN.md as a reviewed stack of PRs.

CONTEXT: DEVELOPMENT_PLAN.md §6 Section G M36 + source .docs/2026-07-26-learning-loop-enhancement-plan.md §2.2 (G10-G13) and .docs/scan64-system-design.md §9.4-9.5 (habits), §12.4 (opening families). Preconditions: M34 merged. Repo: Python 3.12+/uv, FastAPI + SQLModel, pytest/ruff/mypy --strict, scripts/check.sh.
M31 OWNERSHIP CONTRACT (H-002): reports must treat `Game.owner_player_id` as the imported- and played-game corpus boundary, not infer import ownership from `PlaySession`.
OBJECTIVE: The patterns, openings, and weekly report endpoints compute from persisted evidence and games instead of returning literals. Acceptance: a player with three games sharing one diagnosis has that pattern in /patterns with an occurrence count and evidence references; /openings reflects that player's actual opening families; /reports/weekly returns a typed object whose current-mastery fields exclude retired skills; no handler in src/scan64/api/reports.py returns a hardcoded literal collection or string.
RELEASE TRAIN: target=unversioned - "learning loop closed"; included milestones=M31-M37; preparation trigger=all included milestones externally merged; required artifacts=none (DEVELOPMENT_PLAN.md §2 GAP on release policy); release verification=scripts/check.sh exits 0 on main after the final merge plus M37's manual loop walkthrough; publication=not requested.

PRE-IMPLEMENTATION DESIGN GATE:
1. Read DEVELOPMENT_PLAN.md §6 M36, its §1 source-map rows, this prompt, and .docs/DEVELOPMENT_PLAN_HISTORY.md when present.
2. Inspect src/scan64/api/reports.py:125-129 and its weekly handler, src/scan64/learning/profiling/habits.py:90-156, and M34's merged active/retired profile writes with their CI evidence.
3. Revalidate objective, interfaces, dependencies, acceptance, verification, risks, release train, and dependent milestones listed in M36's Design reevaluation row (none).
4. Append one ledger entry to .docs/DEVELOPMENT_PLAN_HISTORY.md: timestamp, milestone, decision, trigger, evidence, plan/prompt sections changed, downstream impact, implementation authorization.
5. If no material mismatch exists, report `DESIGN GO - PLAN REVISION: none`; this authorizes implementation.
6. If a mismatch exists, update both authoritative artifacts for M36 and every affected future milestone, append the revision ID, and report `DESIGN GO - PLAN REVISION: <entry IDs>`. This blocks product code until the reconciliation prerequisite merges.
7. If validity cannot be established, report `DESIGN NO-GO - REASON: <evidence>` and stop. After a reconciliation PR merges, repeat this gate and require `DESIGN GO - PLAN REVISION: none`.

RECONCILIATION RULE: A material revision opens `docs(plan): reconcile M36 design` as a docs-only prerequisite PR. It contains no product code, must be reviewed, green, and externally merged before any code PR, and must not be folded into an implementation PR.

PLANNED STACK (refine only to keep PRs reviewable):
0. Conditional prerequisite `docs(plan): reconcile M36 design` - scope: authoritative plan/prompt updates only; gate: reviewed, green, merged before the implementation stack.
1. PR-1 Recurring habits from persisted diagnoses - scope: wire learning/profiling/habits.py into /v1/players/{id}/patterns with a minimum-occurrence threshold surfaced in the response; commits: "feat(api): compute recurring habits from persisted diagnoses"; verification: `uv run pytest tests/integration/test_reports_from_real_data.py -k patterns`
2. PR-2 Openings report from the player's corpus (on PR-1) - scope: opening-family classification over the player's games with per-family result and error rates; commits: "feat(api): compute the openings report from a player's games"; verification: `uv run pytest tests/integration/test_reports_from_real_data.py -k openings`
3. PR-3 Typed weekly report (on PR-2) - scope: replace the summary string with a typed model carrying games played, active concepts observed, active-mastery deltas, and the top recurring diagnosis; commits: "feat(api): return a typed weekly report"; verification: `uv run pytest tests/integration/test_reports_from_real_data.py -k weekly`

CONSTRAINTS: no scope leakage, minimal dependencies, repo style, no version/changelog updates. Do not change coach-facing aggregation. Distinguish "no habit detected" from "not enough data" in the response rather than returning an empty list for both. Retired state remains historical evidence but must not populate current mastery fields.
VERIFICATION (must pass): `uv run pytest tests/integration/test_reports_from_real_data.py` passes; `scripts/check.sh` exits 0.
REVIEW:
Per PR:
- Scope matches its purpose; contracts match the reconciled plan; behavior is meaningfully tested.
- Sparse-corpus behaviour is explicit, not an accidental empty result.
- History is atomic, conventional, attribution-free, and free of unrelated formatting churn.
- PR-specific verification output is captured.
Whole stack:
- Bases form one valid stack; cumulative acceptance and integration hold; hosted CI is green on every PR; no regression coverage is removed without replacement.
- The docs-only root, when present, is reviewed and green before dependent code PRs.
- Report PR URLs, bases, verification, risks, manual gates, and review completion.
FINAL VERDICTS:
- Report the design verdict before the merge verdict.
- Then report exactly one merge verdict: `GO - RELEASE: unversioned - learning loop closed - RELEASE PREP: pending` or `NO-GO - RELEASE: unversioned - learning loop closed - REASON: <blocking gate>`.
- `GO` requires `DESIGN GO`, every PR correctly based/reviewed/green, local verification, and full milestone acceptance.
DONE: design verdict with evidence; when authorized, a reviewed stack with a release-aware merge verdict and evidence.
```

---

### M37 — Interactive exercises with recorded attempts

```text
/goal Deliver milestone M37 (Interactive exercises with recorded attempts) from DEVELOPMENT_PLAN.md as a reviewed stack of PRs.

CONTEXT: DEVELOPMENT_PLAN.md §6 Section G M37 + source .docs/2026-07-26-learning-loop-enhancement-plan.md §2.3 (G17-G19, G23) and .docs/scan64-system-design.md §10 (exercises), §12.5 (opening missions), §20 (application screens). Preconditions: M34 merged. Repo: Python 3.12+/uv backend; React 19 + TypeScript + Vite + pnpm frontend with chessground and chess.js; Vitest, Playwright; scripts/check.sh.
OBJECTIVE: A learner can attempt any generated lesson on a real board and the attempt is recorded against their profile. Acceptance: Daily Training presents an interactive board, not a FEN string; submitting the accepted move is confirmed and recorded; submitting a wrong move consumes an attempt and reveals the next hint; a served training session has a StudySession row and every attempt links to it; an attempt changes the relevant active SkillState and cannot recreate a retired code; completing an Opening Explorer mission records an attempt server-side rather than being discarded on navigation.
RELEASE TRAIN: target=unversioned - "learning loop closed"; included milestones=M31-M37; preparation trigger=all included milestones externally merged; required artifacts=none (DEVELOPMENT_PLAN.md §2 GAP on release policy); release verification=scripts/check.sh exits 0 on main after the final merge plus this milestone's manual loop walkthrough; publication=not requested. This is the final milestone of the train - report release preparation as due once it merges.

PRE-IMPLEMENTATION DESIGN GATE:
1. Read DEVELOPMENT_PLAN.md §6 M37, its §1 source-map rows, this prompt, and .docs/DEVELOPMENT_PLAN_HISTORY.md when present.
2. Inspect apps/scan64-web/src/components/DailyTrainingScreen.tsx:75-90, CriticalMomentReview.tsx:75-129, PlayScreen.tsx's working chessground configuration including its bounds recompute, src/scan64/api/learning.py:250-257, src/scan64/content/models.py:39-68, and M34's merged profile-update service with active/retired taxonomy state.
3. Revalidate objective, interfaces, dependencies, acceptance, verification, risks, release train, and every dependent milestone listed in M37's Design reevaluation row (M38, M43).
4. Append one ledger entry to .docs/DEVELOPMENT_PLAN_HISTORY.md: timestamp, milestone, decision, trigger, evidence, plan/prompt sections changed, downstream impact, implementation authorization.
5. If no material mismatch exists, report `DESIGN GO - PLAN REVISION: none`; this authorizes implementation.
6. If a mismatch exists, update both authoritative artifacts for M37 and every affected future milestone, append the revision ID, and report `DESIGN GO - PLAN REVISION: <entry IDs>`. This blocks product code until the reconciliation prerequisite merges.
7. If validity cannot be established, report `DESIGN NO-GO - REASON: <evidence>` and stop. After a reconciliation PR merges, repeat this gate and require `DESIGN GO - PLAN REVISION: none`.

RECONCILIATION RULE: A material revision opens `docs(plan): reconcile M37 design` as a docs-only prerequisite PR. It contains no product code, must be reviewed, green, and externally merged before any code PR, and must not be folded into an implementation PR.

PLANNED STACK (refine only to keep PRs reviewable):
0. Conditional prerequisite `docs(plan): reconcile M37 design` - scope: authoritative plan/prompt updates only; gate: reviewed, green, merged before the implementation stack.
1. PR-1 Generic attempt endpoint and StudySession - scope: POST /v1/lesson-attempts accepting lesson id, session id, submitted move, elapsed time, hints used; persist a StudySession when /v1/learning/session is served; reuse M34's profile-update service; record an attempt for a retired skill with an explicit skipped-profile result rather than recreating or rejecting it; commits: "feat(api): persist a study session for every served training session", "feat(api): record a generic lesson attempt"; verification: `uv run pytest tests/integration/test_lesson_attempts.py`
2. PR-2 Shared lesson board component (on PR-1) - scope: extract LessonBoard from the working play-board configuration, rendering a LessonSpec position with legal-move input; commits: "feat(web): add a shared lesson board component"; verification: `pnpm --dir apps/scan64-web test`
3. PR-3 Daily Training on the lesson board (on PR-2) - scope: replace the raw-FEN rendering with the board, hint ladder, and accept/reject feedback wired to the attempt endpoint; commits: "feat(web): make daily training interactive"; verification: `pnpm --dir apps/scan64-web test`
4. PR-4 Critical Moment Review on the lesson board (on PR-3) - scope: same treatment for the in-game interruption review; commits: "feat(web): let a player answer a critical-moment review"; verification: `pnpm --dir apps/scan64-web test`
5. PR-5 Opening Explorer missions on the lesson board (on PR-4) - scope: submit mission answers through the generic attempt endpoint instead of evaluating them client-side against local seed data; commits: "feat(web): record opening-mission attempts server-side"; verification: `pnpm --dir apps/scan64-web test`
6. PR-6 Pointer-driven attempt e2e (on PR-5) - scope: a Playwright spec answering a lesson with real mouse events and asserting the recorded attempt; commits: "test(web): answer a training lesson with real pointer input"; verification: `pnpm --dir apps/scan64-web test:e2e`

CONSTRAINTS: no scope leakage, minimal dependencies, repo style, no version/changelog updates. Extract the shared board from the play board's working configuration - do not re-derive chessground setup, and keep the bounds recompute that makes the board usable. Do not change session composition; that is M38. Do not replace the Opening Explorer's seed families with a server-side catalog; only the attempt submission moves server-side. Do not silently recreate a taxonomy skill retired by M34 or discard the learner's valid attempt.
VERIFICATION (must pass): `uv run pytest tests/integration/test_lesson_attempts.py` passes; `pnpm --dir apps/scan64-web test` and `pnpm --dir apps/scan64-web test:e2e` pass; `scripts/check.sh` exits 0; manual - complete one Daily Training lesson and one Opening Explorer mission end to end and observe the mastery change in GET /v1/players/{id}/progress.
REVIEW:
Per PR:
- Scope matches its purpose; contracts match the reconciled plan; behavior is meaningfully tested.
- The extraction does not regress the play board; both boards accept real pointer input.
- History is atomic, conventional, attribution-free, and free of unrelated formatting churn.
- PR-specific verification output is captured.
Whole stack:
- Bases form one valid stack; cumulative acceptance and integration hold; hosted CI is green on every PR; no regression coverage is removed without replacement.
- The docs-only root, when present, is reviewed and green before dependent code PRs.
- Report PR URLs, bases, verification, risks, manual gates, and review completion.
FINAL VERDICTS:
- Report the design verdict before the merge verdict.
- Then report exactly one merge verdict: `GO - RELEASE: unversioned - learning loop closed - RELEASE PREP: pending` or `NO-GO - RELEASE: unversioned - learning loop closed - REASON: <blocking gate>`.
- `GO` requires `DESIGN GO`, every PR correctly based/reviewed/green, local verification, and full milestone acceptance. On `GO`, note that this milestone completes the train and release preparation is now due.
DONE: design verdict with evidence; when authorized, a reviewed stack with a release-aware merge verdict and evidence.
```

---

### M38 — Adaptive session driven by actual state

```text
/goal Deliver milestone M38 (Adaptive session driven by actual state) from DEVELOPMENT_PLAN.md as a reviewed stack of PRs.

CONTEXT: DEVELOPMENT_PLAN.md §6 Section H M38 + source .docs/2026-07-26-learning-loop-enhancement-plan.md §2.2 (G16) and .docs/scan64-system-design.md §21 (scheduler), §9 (player model). Preconditions: M37 merged. Repo: Python 3.12+/uv, FastAPI + SQLModel, pytest/ruff/mypy --strict, scripts/check.sh.
OBJECTIVE: The training session a learner receives is composed from measured active mastery, due non-retired reviews, and recent fatigue rather than fixed constants. Acceptance: a low-mastery active concept's lessons outrank a high-mastery concept's; overdue non-retired reviews outrank exploration items; after a long high-error session fatigue measurably shifts composition; no constant priority factor remains in the request path; the exploration floor guarantees at least one non-weakness item per session.
RELEASE TRAIN: target=unversioned - "adaptive and operable"; included milestones=M38-M44; preparation trigger=all included milestones externally merged; required artifacts=none (DEVELOPMENT_PLAN.md §2 GAP on release policy); release verification=scripts/check.sh exits 0 on main after the final merge plus M44's clean-clone quickstart walkthrough; publication=not requested.

PRE-IMPLEMENTATION DESIGN GATE:
1. Read DEVELOPMENT_PLAN.md §6 M38, its §1 source-map rows, this prompt, and .docs/DEVELOPMENT_PLAN_HISTORY.md when present.
2. Inspect src/scan64/api/learning.py:199-257 and the merged M34/M37 writes to active SkillState, non-retired ReviewSchedule, ContentAttempt, and StudySession, with their CI evidence.
3. Revalidate objective, interfaces, dependencies, acceptance, verification, risks, release train, and the dependent milestone listed in M38's Design reevaluation row (M43).
4. Append one ledger entry to .docs/DEVELOPMENT_PLAN_HISTORY.md: timestamp, milestone, decision, trigger, evidence, plan/prompt sections changed, downstream impact, implementation authorization.
5. If no material mismatch exists, report `DESIGN GO - PLAN REVISION: none`; this authorizes implementation.
6. If a mismatch exists, update both authoritative artifacts for M38 and every affected future milestone, append the revision ID, and report `DESIGN GO - PLAN REVISION: <entry IDs>`. This blocks product code until the reconciliation prerequisite merges.
7. If validity cannot be established, report `DESIGN NO-GO - REASON: <evidence>` and stop. After a reconciliation PR merges, repeat this gate and require `DESIGN GO - PLAN REVISION: none`.

RECONCILIATION RULE: A material revision opens `docs(plan): reconcile M38 design` as a docs-only prerequisite PR. It contains no product code, must be reviewed, green, and externally merged before any code PR, and must not be folded into an implementation PR.

PLANNED STACK (refine only to keep PRs reviewable):
0. Conditional prerequisite `docs(plan): reconcile M38 design` - scope: authoritative plan/prompt updates only; gate: reviewed, green, merged before the implementation stack.
1. PR-1 Load real profile state - scope: load active SkillState, due non-retired ReviewSchedule rows, and recent attempt history in the session request path; commits: "feat(api): load real profile state when composing a session"; verification: `uv run pytest tests/integration/test_adaptive_session.py -k loads_state`
2. PR-2 Computed priority factors (on PR-1) - scope: replace the hardcoded weakness/interest/relevance constants with computed values, documenting each factor's source; commits: "feat(learning): compute session priority from measured state"; verification: `uv run pytest tests/integration/test_adaptive_session.py -k priority`
3. PR-3 Session fatigue (on PR-2) - scope: derive fatigue from recent attempt volume and accuracy and feed it into compute_priority; commits: "feat(learning): derive session fatigue from recent attempts"; verification: `uv run pytest tests/integration/test_adaptive_session.py -k fatigue`
4. PR-4 Exploration floor (on PR-3) - scope: guarantee at least one non-weakness item per session; commits: "feat(learning): guarantee an exploration item in every session"; verification: `uv run pytest tests/integration/test_adaptive_session.py -k exploration`

CONSTRAINTS: no scope leakage, minimal dependencies, repo style, no version/changelog updates. Add no new exercise types. Transfer selection is M43. The exploration floor is part of this milestone, not a follow-up. Retired state is historical and must not enter session composition.
VERIFICATION (must pass): `uv run pytest tests/integration/test_adaptive_session.py` passes; `scripts/check.sh` exits 0.
REVIEW:
Per PR:
- Scope matches its purpose; contracts match the reconciled plan; behavior is meaningfully tested.
- Weighting is documented with each factor's source; no magic constant survives unexplained.
- History is atomic, conventional, attribution-free, and free of unrelated formatting churn.
- PR-specific verification output is captured.
Whole stack:
- Bases form one valid stack; cumulative acceptance and integration hold; hosted CI is green on every PR; no regression coverage is removed without replacement.
- The docs-only root, when present, is reviewed and green before dependent code PRs.
- Report PR URLs, bases, verification, risks, manual gates, and review completion.
FINAL VERDICTS:
- Report the design verdict before the merge verdict.
- Then report exactly one merge verdict: `GO - RELEASE: unversioned - adaptive and operable - RELEASE PREP: pending` or `NO-GO - RELEASE: unversioned - adaptive and operable - REASON: <blocking gate>`.
- `GO` requires `DESIGN GO`, every PR correctly based/reviewed/green, local verification, and full milestone acceptance.
DONE: design verdict with evidence; when authorized, a reviewed stack with a release-aware merge verdict and evidence.
```

---

### M39 — Your games, your history, real navigation

```text
/goal Deliver milestone M39 (Your games, your history, real navigation) from DEVELOPMENT_PLAN.md as a reviewed stack of PRs.

CONTEXT: DEVELOPMENT_PLAN.md §6 Section H M39 + source .docs/2026-07-26-learning-loop-enhancement-plan.md §2.3 (G20, G21) and .docs/scan64-system-design.md §20 (application screens). Preconditions: M32 merged. Repo: React 19 + TypeScript + Vite + pnpm, Vitest, Playwright, oxlint; scripts/check.sh.
M31 OWNERSHIP CONTRACT (H-002): M32's games API must list by `Game.owner_player_id`, so imported games remain visible even though they have no `PlaySession`.
OBJECTIVE: A learner can find their past games, open one, and see its diagnoses, without losing an in-progress game to a stray navigation click. Acceptance: navigating away from an active game and back resumes the same position; the games list shows every game the player played or imported; a game's analysis view is reachable by URL and renders its persisted diagnoses; a browser reload during a game resumes rather than restarting.
RELEASE TRAIN: target=unversioned - "adaptive and operable"; included milestones=M38-M44; preparation trigger=all included milestones externally merged; required artifacts=none (DEVELOPMENT_PLAN.md §2 GAP on release policy); release verification=scripts/check.sh exits 0 on main after the final merge plus M44's clean-clone quickstart walkthrough; publication=not requested.

PRE-IMPLEMENTATION DESIGN GATE:
1. Read DEVELOPMENT_PLAN.md §6 M39, its §1 source-map rows, this prompt, and .docs/DEVELOPMENT_PLAN_HISTORY.md when present.
2. Inspect apps/scan64-web/src/App.tsx:14-77, each screen's local state, and M32's merged GET /v1/players/{id}/games with its CI evidence.
3. Revalidate objective, interfaces, dependencies, acceptance, verification, risks, release train, and the dependent milestone listed in M39's Design reevaluation row (M44).
4. Append one ledger entry to .docs/DEVELOPMENT_PLAN_HISTORY.md: timestamp, milestone, decision, trigger, evidence, plan/prompt sections changed, downstream impact, implementation authorization.
5. If no material mismatch exists, report `DESIGN GO - PLAN REVISION: none`; this authorizes implementation.
6. If a mismatch exists, update both authoritative artifacts for M39 and every affected future milestone, append the revision ID, and report `DESIGN GO - PLAN REVISION: <entry IDs>`. This blocks product code until the reconciliation prerequisite merges.
7. If validity cannot be established, report `DESIGN NO-GO - REASON: <evidence>` and stop. After a reconciliation PR merges, repeat this gate and require `DESIGN GO - PLAN REVISION: none`.

RECONCILIATION RULE: A material revision opens `docs(plan): reconcile M39 design` as a docs-only prerequisite PR. It contains no product code, must be reviewed, green, and externally merged before any code PR, and must not be folded into an implementation PR.

PLANNED STACK (refine only to keep PRs reviewable):
0. Conditional prerequisite `docs(plan): reconcile M39 design` - scope: authoritative plan/prompt updates only; gate: reviewed, green, merged before the implementation stack.
1. PR-1 URL routing with screens unchanged - scope: replace the single currentView state with routing, preserving every screen's current behaviour; commits: "feat(web): route screens by URL"; verification: `pnpm --dir apps/scan64-web test`
2. PR-2 Play-session resumption (on PR-1) - scope: persist the active session id and resume position on reload or navigation return; commits: "feat(web): resume an in-progress game after navigation or reload"; verification: `pnpm --dir apps/scan64-web test:e2e`
3. PR-3 Games list screen (on PR-2) - scope: GamesListScreen backed by GET /v1/players/{id}/games showing result, date, and diagnosis count; commits: "feat(web): list a player's games"; verification: `pnpm --dir apps/scan64-web test`
4. PR-4 Deep links to game analysis (on PR-3) - scope: per-game analysis route rendering persisted diagnoses; commits: "feat(web): open a game's analysis by URL"; verification: `pnpm --dir apps/scan64-web test:e2e`

CONSTRAINTS: no scope leakage, minimal dependencies, repo style, no version/changelog updates. Land routing before any screen change so the diff stays reviewable. Do not add PGN export UI beyond what the data-lifecycle endpoints already provide.
VERIFICATION (must pass): `pnpm --dir apps/scan64-web test` and `pnpm --dir apps/scan64-web test:e2e` pass, including a navigate-away-and-resume spec; `scripts/check.sh` exits 0.
REVIEW:
Per PR:
- Scope matches its purpose; contracts match the reconciled plan; behavior is meaningfully tested.
- Routing does not silently change any screen's behaviour in the same PR that introduces it.
- History is atomic, conventional, attribution-free, and free of unrelated formatting churn.
- PR-specific verification output is captured.
Whole stack:
- Bases form one valid stack; cumulative acceptance and integration hold; hosted CI is green on every PR; no regression coverage is removed without replacement.
- The docs-only root, when present, is reviewed and green before dependent code PRs.
- Report PR URLs, bases, verification, risks, manual gates, and review completion.
FINAL VERDICTS:
- Report the design verdict before the merge verdict.
- Then report exactly one merge verdict: `GO - RELEASE: unversioned - adaptive and operable - RELEASE PREP: pending` or `NO-GO - RELEASE: unversioned - adaptive and operable - REASON: <blocking gate>`.
- `GO` requires `DESIGN GO`, every PR correctly based/reviewed/green, local verification, and full milestone acceptance.
DONE: design verdict with evidence; when authorized, a reviewed stack with a release-aware merge verdict and evidence.
```

---

### M40 — Analysis board shows real engine evaluation

```text
/goal Deliver milestone M40 (Analysis board shows real engine evaluation) from DEVELOPMENT_PLAN.md as a reviewed stack of PRs.

CONTEXT: DEVELOPMENT_PLAN.md §6 Section H M40 + source .docs/2026-07-26-learning-loop-enhancement-plan.md §2.3 (G22) and .docs/scan64-system-design.md §20 (application screens). Preconditions: M31 merged. Repo: React 19 + TypeScript + Vite + pnpm, Vitest, Playwright; scripts/check.sh.
M31 OWNERSHIP CONTRACT (H-002): ownerless legacy games are neither analysable nor safely attributable. Render this as a distinct honest state, not an "analyse" action that fails.
OBJECTIVE: The analysis board renders persisted engine evaluations and diagnoses instead of reporting that no analysis is available. Acceptance: an analysed game shows an evaluation for every persisted position and a marker at each diagnosed position; an unanalysed owned game offers an analyse action rather than a dead message; a game analysed with no findings says so explicitly; an ownerless legacy game explains why analysis is unavailable.
RELEASE TRAIN: target=unversioned - "adaptive and operable"; included milestones=M38-M44; preparation trigger=all included milestones externally merged; required artifacts=none (DEVELOPMENT_PLAN.md §2 GAP on release policy); release verification=scripts/check.sh exits 0 on main after the final merge plus M44's clean-clone quickstart walkthrough; publication=not requested.

PRE-IMPLEMENTATION DESIGN GATE:
1. Read DEVELOPMENT_PLAN.md §6 M40, its §1 source-map rows, this prompt, and .docs/DEVELOPMENT_PLAN_HISTORY.md when present.
2. Inspect apps/scan64-web/src/components/AnalysisScreen.tsx:32-183 and M31's merged /v1/games/{id}/positions payload shape with its CI evidence; confirm the payload carries the evaluation and diagnosis data this screen needs.
3. Revalidate objective, interfaces, dependencies, acceptance, verification, risks, release train, and dependent milestones listed in M40's Design reevaluation row (none).
4. Append one ledger entry to .docs/DEVELOPMENT_PLAN_HISTORY.md: timestamp, milestone, decision, trigger, evidence, plan/prompt sections changed, downstream impact, implementation authorization.
5. If no material mismatch exists, report `DESIGN GO - PLAN REVISION: none`; this authorizes implementation.
6. If a mismatch exists, update both authoritative artifacts for M40 and every affected future milestone, append the revision ID, and report `DESIGN GO - PLAN REVISION: <entry IDs>`. This blocks product code until the reconciliation prerequisite merges.
7. If validity cannot be established, report `DESIGN NO-GO - REASON: <evidence>` and stop. After a reconciliation PR merges, repeat this gate and require `DESIGN GO - PLAN REVISION: none`.

RECONCILIATION RULE: A material revision opens `docs(plan): reconcile M40 design` as a docs-only prerequisite PR. It contains no product code, must be reviewed, green, and externally merged before any code PR, and must not be folded into an implementation PR.

PLANNED STACK (refine only to keep PRs reviewable):
0. Conditional prerequisite `docs(plan): reconcile M40 design` - scope: authoritative plan/prompt updates only; gate: reviewed, green, merged before the implementation stack.
1. PR-1 Render persisted evaluations - scope: per-position evaluation display and diagnosis markers on the move list; commits: "feat(web): show persisted engine evaluations on the analysis board"; verification: `pnpm --dir apps/scan64-web test`
2. PR-2 Honest empty and ownership states (on PR-1) - scope: distinguish not-analysed, analysed-with-no-findings, and ownerless legacy games; offer an analyse action only for the first; commits: "feat(web): explain unavailable analysis for an ownerless legacy game"; verification: `pnpm --dir apps/scan64-web test`

CONSTRAINTS: no scope leakage, minimal dependencies, repo style, no version/changelog updates. Do not add on-demand interactive engine analysis of arbitrary user-entered positions.
VERIFICATION (must pass): `pnpm --dir apps/scan64-web test` passes; `scripts/check.sh` exits 0; manual - open a locally analysed game and confirm evaluations and markers render.
REVIEW:
Per PR:
- Scope matches its purpose; contracts match the reconciled plan; behavior is meaningfully tested.
- Empty states are honest and distinguishable rather than one generic message.
- History is atomic, conventional, attribution-free, and free of unrelated formatting churn.
- PR-specific verification output is captured.
Whole stack:
- Bases form one valid stack; cumulative acceptance and integration hold; hosted CI is green on every PR; no regression coverage is removed without replacement.
- The docs-only root, when present, is reviewed and green before dependent code PRs.
- Report PR URLs, bases, verification, risks, manual gates, and review completion.
FINAL VERDICTS:
- Report the design verdict before the merge verdict.
- Then report exactly one merge verdict: `GO - RELEASE: unversioned - adaptive and operable - RELEASE PREP: pending` or `NO-GO - RELEASE: unversioned - adaptive and operable - REASON: <blocking gate>`.
- `GO` requires `DESIGN GO`, every PR correctly based/reviewed/green, local verification, and full milestone acceptance.
DONE: design verdict with evidence; when authorized, a reviewed stack with a release-aware merge verdict and evidence.
```

---

### M41 — Engine pool and admission control on the production path

```text
/goal Deliver milestone M41 (Engine pool and admission control on the production path) from DEVELOPMENT_PLAN.md as a reviewed stack of PRs.

CONTEXT: DEVELOPMENT_PLAN.md §6 Section I M41 + source .docs/2026-07-26-learning-loop-enhancement-plan.md §2.4 (G26, G27) and .docs/scan64-system-design.md §18.6 (compute budgets). Preconditions: M31 merged; M32's interim in-flight cap is superseded here. Repo: Python 3.12+/uv, FastAPI, pinned Stockfish 18 in CI, pytest/ruff/mypy --strict, scripts/check.sh.
M31 OWNERSHIP CONTRACT (H-002): admission control resolves the player from `Game.owner_player_id` at job submission; an ownerless legacy game is rejected loudly.
OBJECTIVE: Interactive play is never queued behind batch analysis and no player can monopolise analysis capacity. Acceptance: a move request issued during a running batch analysis completes within the interactive budget; a player exceeding the daily quota has jobs queued fair-share, never rejected or dropped; process count stays bounded under concurrent play plus analysis; a pooled engine carries no state between analyses.
RELEASE TRAIN: target=unversioned - "adaptive and operable"; included milestones=M38-M44; preparation trigger=all included milestones externally merged; required artifacts=none (DEVELOPMENT_PLAN.md §2 GAP on release policy); release verification=scripts/check.sh exits 0 on main after the final merge plus M44's clean-clone quickstart walkthrough; publication=not requested.

PRE-IMPLEMENTATION DESIGN GATE:
1. Read DEVELOPMENT_PLAN.md §6 M41, its §1 source-map rows, this prompt, and .docs/DEVELOPMENT_PLAN_HISTORY.md when present.
2. Inspect src/scan64/providers/stockfish/pool.py:78-151, src/scan64/chess/analysis/admission.py:14-86, src/scan64/api/games.py:158-168, src/scan64/chess/opponents/stockfish_opponent.py:22-28, and M32's merged auto-enqueue including its in-flight cap.
3. Revalidate objective, interfaces, dependencies, acceptance, verification, risks, release train, and dependent milestones listed in M41's Design reevaluation row (none). Confirm whether M32's cap should be removed in this stack.
4. Append one ledger entry to .docs/DEVELOPMENT_PLAN_HISTORY.md: timestamp, milestone, decision, trigger, evidence, plan/prompt sections changed, downstream impact, implementation authorization.
5. If no material mismatch exists, report `DESIGN GO - PLAN REVISION: none`; this authorizes implementation.
6. If a mismatch exists, update both authoritative artifacts for M41 and every affected future milestone, append the revision ID, and report `DESIGN GO - PLAN REVISION: <entry IDs>`. This blocks product code until the reconciliation prerequisite merges.
7. If validity cannot be established, report `DESIGN NO-GO - REASON: <evidence>` and stop. After a reconciliation PR merges, repeat this gate and require `DESIGN GO - PLAN REVISION: none`.

RECONCILIATION RULE: A material revision opens `docs(plan): reconcile M41 design` as a docs-only prerequisite PR. It contains no product code, must be reviewed, green, and externally merged before any code PR, and must not be folded into an implementation PR.

PLANNED STACK (refine only to keep PRs reviewable):
0. Conditional prerequisite `docs(plan): reconcile M41 design` - scope: authoritative plan/prompt updates only; gate: reviewed, green, merged before the implementation stack.
1. PR-1 Pool lifecycle - scope: bind EnginePoolManager to the FastAPI lifespan with documented interactive and batch concurrency limits and an engine reset on checkout; commits: "feat(providers): manage stockfish engines in lifespan-bound pools"; verification: `uv run pytest tests/integration/test_engine_pool_isolation.py -k lifecycle`
2. PR-2 Interactive pool for play (on PR-1) - scope: route the opponent provider through the interactive pool; commits: "feat(play): serve opponent moves from the interactive engine pool"; verification: `uv run pytest tests/integration/test_engine_pool_isolation.py`
3. PR-3 Batch pool for analysis (on PR-2) - scope: route analysis jobs through the batch pool; commits: "feat(analysis): run analysis on the batch engine pool"; verification: `uv run pytest tests/integration/test_engine_pool_isolation.py -k isolation`
4. PR-4 Admission control (on PR-3) - scope: enforce the per-player daily quota with fair-share queueing at job submission, returning a queued status rather than an error; remove M32's interim in-flight cap; commits: "feat(analysis): admit analysis jobs under a per-player quota", "refactor(play): drop the interim in-flight cap"; verification: `uv run pytest tests/integration/test_admission_control.py`

CONSTRAINTS: no scope leakage, minimal dependencies, repo style, no version/changelog updates. Do not build distributed or multi-host scheduling. Do not leave two throttling mechanisms in place. Retain per-call adapter construction behind a configuration flag for one release.
VERIFICATION (must pass): `uv run pytest tests/integration/test_engine_pool_isolation.py tests/integration/test_admission_control.py` passes; `scripts/check.sh` exits 0.
REVIEW:
Per PR:
- Scope matches its purpose; contracts match the reconciled plan; behavior is meaningfully tested.
- The engine reset on checkout is asserted by a test, not assumed; quota exhaustion queues rather than drops.
- History is atomic, conventional, attribution-free, and free of unrelated formatting churn.
- PR-specific verification output is captured.
Whole stack:
- Bases form one valid stack; cumulative acceptance and integration hold; hosted CI is green on every PR; no regression coverage is removed without replacement.
- The docs-only root, when present, is reviewed and green before dependent code PRs.
- Report PR URLs, bases, verification, risks, manual gates, and review completion.
FINAL VERDICTS:
- Report the design verdict before the merge verdict.
- Then report exactly one merge verdict: `GO - RELEASE: unversioned - adaptive and operable - RELEASE PREP: pending` or `NO-GO - RELEASE: unversioned - adaptive and operable - REASON: <blocking gate>`.
- `GO` requires `DESIGN GO`, every PR correctly based/reviewed/green, local verification, and full milestone acceptance.
DONE: design verdict with evidence; when authorized, a reviewed stack with a release-aware merge verdict and evidence.
```

---

### M42 — Complete the data-lifecycle contract

```text
/goal Deliver milestone M42 (Complete the data-lifecycle contract) from DEVELOPMENT_PLAN.md as a reviewed stack of PRs.

CONTEXT: DEVELOPMENT_PLAN.md §6 Section I M42 + source .docs/2026-07-26-learning-loop-enhancement-plan.md §2.4 (G28) and .docs/scan64-system-design.md §24.1 (privacy, export, deletion). Preconditions: M31 and M34 merged. Repo: Python 3.12+/uv, FastAPI + SQLModel + SQLite, pytest/ruff/mypy --strict, scripts/check.sh.
M31 OWNERSHIP CONTRACT (H-002): the export/import/deletion completeness set includes `Game.owner_player_id` as player-derived ownership data in addition to the M31 analysis rows and M34's player-scoped `ProfileObservation`.
OBJECTIVE: Export, import, and deletion cover every table holding player-derived data, including the evidence M31 begins writing and M34's profile observations. Acceptance: export-delete-import round-trips a player with analysed games and profile observations and leaves no orphan rows; after deletion every player-scoped table has zero residual rows for that player, asserted per table rather than inferred from a success status; adding a new player-scoped table without registering it fails the completeness test.
RELEASE TRAIN: target=unversioned - "adaptive and operable"; included milestones=M38-M44; preparation trigger=all included milestones externally merged; required artifacts=none (DEVELOPMENT_PLAN.md §2 GAP on release policy); release verification=scripts/check.sh exits 0 on main after the final merge plus M44's clean-clone quickstart walkthrough; publication=not requested.

HUMAN REVIEW GATE: Do not merge or run destructive paths unattended until a human reviews dry-run output, rollback notes, and audit/tombstone logging.

PRE-IMPLEMENTATION DESIGN GATE:
1. Read DEVELOPMENT_PLAN.md §6 M42, its §1 source-map rows, this prompt, and .docs/DEVELOPMENT_PLAN_HISTORY.md when present.
2. Inspect src/scan64/api/data_lifecycle.py:23-46,118-130,353-365, the DeletionAudit writer at :367-378, the full SQLModel table set, M31's merged evidence writes, and M34's ProfileObservation schema with their CI evidence.
3. Revalidate objective, interfaces, dependencies, acceptance, verification, risks, release train, and dependent milestones listed in M42's Design reevaluation row (none). Confirm the set of player-scoped tables has not changed since the plan was written.
4. Append one ledger entry to .docs/DEVELOPMENT_PLAN_HISTORY.md: timestamp, milestone, decision, trigger, evidence, plan/prompt sections changed, downstream impact, implementation authorization.
5. If no material mismatch exists, report `DESIGN GO - PLAN REVISION: none`; this authorizes implementation.
6. If a mismatch exists, update both authoritative artifacts for M42 and every affected future milestone, append the revision ID, and report `DESIGN GO - PLAN REVISION: <entry IDs>`. This blocks product code until the reconciliation prerequisite merges.
7. If validity cannot be established, report `DESIGN NO-GO - REASON: <evidence>` and stop. After a reconciliation PR merges, repeat this gate and require `DESIGN GO - PLAN REVISION: none`.

RECONCILIATION RULE: A material revision opens `docs(plan): reconcile M42 design` as a docs-only prerequisite PR. It contains no product code, must be reviewed, green, and externally merged before any code PR, and must not be folded into an implementation PR.

PLANNED STACK (refine only to keep PRs reviewable):
0. Conditional prerequisite `docs(plan): reconcile M42 design` - scope: authoritative plan/prompt updates only; gate: reviewed, green, merged before the implementation stack.
1. PR-1 Completeness test - scope: tests/integration/test_data_lifecycle_completeness.py enumerating SQLModel tables and failing for any player-scoped table absent from export, import, or deletion; lands demonstrating the current gap; commits: "test(privacy): require every player-scoped table in the lifecycle contract"; verification: `uv run pytest tests/integration/test_data_lifecycle_completeness.py`
2. PR-2 Export and import coverage (on PR-1) - scope: add Evidence, Position, EngineAnalysis, ProfileObservation, TransferPosition, TransferMeasurement, and StudySession to the archive schema and import path; commits: "feat(privacy): export and import analysis and study data"; verification: `uv run pytest tests/integration -k lifecycle_roundtrip`
3. PR-3 Deletion coverage and audit (on PR-2) - scope: delete the added tables with per-table residual assertions and updated DeletionAudit records; commits: "feat(privacy): delete analysis and study data with the player", "feat(privacy): record deleted table coverage in the audit"; verification: `uv run pytest tests/integration/test_data_lifecycle_completeness.py -k deletion`

CONSTRAINTS: no scope leakage, minimal dependencies, repo style, no version/changelog updates. Do not define a hosted-mode retention policy; that is a documented GAP. Assert zero residual rows per table - never infer deletion success from a status code. Include ProfileObservation in every lifecycle path.
VERIFICATION (must pass): `uv run pytest tests/integration/test_data_lifecycle_completeness.py` passes with per-table residual assertions; `scripts/check.sh` exits 0; manual - dry-run a deletion and review the audit output before running the destructive path.
REVIEW:
Per PR:
- Scope matches its purpose; contracts match the reconciled plan; behavior is meaningfully tested.
- Deletion is asserted per table; the audit records what was removed; dry-run output is reviewable.
- History is atomic, conventional, attribution-free, and free of unrelated formatting churn.
- PR-specific verification output is captured.
Whole stack:
- Bases form one valid stack; cumulative acceptance and integration hold; hosted CI is green on every PR; no regression coverage is removed without replacement.
- The human review gate is satisfied before any destructive path runs outside a test fixture.
- Report PR URLs, bases, verification, risks, manual gates, and review completion.
FINAL VERDICTS:
- Report the design verdict before the merge verdict.
- Then report exactly one merge verdict: `GO - RELEASE: unversioned - adaptive and operable - RELEASE PREP: pending` or `NO-GO - RELEASE: unversioned - adaptive and operable - REASON: <blocking gate>`.
- `GO` requires `DESIGN GO`, every PR correctly based/reviewed/green, local verification, full milestone acceptance, and a satisfied human review gate.
DONE: design verdict with evidence; when authorized, a reviewed stack with a release-aware merge verdict and evidence.
```

---

### M43 — Transfer measurement becomes reachable

```text
/goal Deliver milestone M43 (Transfer measurement becomes reachable) from DEVELOPMENT_PLAN.md as a reviewed stack of PRs.

CONTEXT: DEVELOPMENT_PLAN.md §6 Section I M43 + source .docs/2026-07-26-learning-loop-enhancement-plan.md §2.4 (G29, G30) and .docs/scan64-system-design.md §10.6 (transfer exercises), §23.4-23.5 (learning metrics). Preconditions: M38 merged. Repo: Python 3.12+/uv, FastAPI + SQLModel, pinned Stockfish 18 in CI, pytest/ruff/mypy --strict, scripts/check.sh.
OBJECTIVE: The transfer-measurement lifecycle is usable through the API instead of only through tests, and lesson verification confirms the accepted move actually answers the objective. Acceptance: reaching the mastery threshold on an active concept assigns a transfer position; it appears in a later session as a due item; completing it records a measurement and moves the transfer report; a lesson whose accepted move is not engine-best fails verification; previously persisted lessons are re-verified on read and marked, never deleted.
RELEASE TRAIN: target=unversioned - "adaptive and operable"; included milestones=M38-M44; preparation trigger=all included milestones externally merged; required artifacts=none (DEVELOPMENT_PLAN.md §2 GAP on release policy); release verification=scripts/check.sh exits 0 on main after the final merge plus M44's clean-clone quickstart walkthrough; publication=not requested.

PRE-IMPLEMENTATION DESIGN GATE:
1. Read DEVELOPMENT_PLAN.md §6 M43, its §1 source-map rows, this prompt, and .docs/DEVELOPMENT_PLAN_HISTORY.md when present.
2. Inspect src/scan64/learning/evaluation/transfer_measurement.py:41-162, src/scan64/learning/exercises/transfer.py:13-141, src/scan64/learning/verification/verifier.py:26-40, and the merged M37/M38 active attempt and session paths with their CI evidence.
3. Revalidate objective, interfaces, dependencies, acceptance, verification, risks, release train, and dependent milestones listed in M43's Design reevaluation row (none).
4. Append one ledger entry to .docs/DEVELOPMENT_PLAN_HISTORY.md: timestamp, milestone, decision, trigger, evidence, plan/prompt sections changed, downstream impact, implementation authorization.
5. If no material mismatch exists, report `DESIGN GO - PLAN REVISION: none`; this authorizes implementation.
6. If a mismatch exists, update both authoritative artifacts for M43 and every affected future milestone, append the revision ID, and report `DESIGN GO - PLAN REVISION: <entry IDs>`. This blocks product code until the reconciliation prerequisite merges.
7. If validity cannot be established, report `DESIGN NO-GO - REASON: <evidence>` and stop. After a reconciliation PR merges, repeat this gate and require `DESIGN GO - PLAN REVISION: none`.

RECONCILIATION RULE: A material revision opens `docs(plan): reconcile M43 design` as a docs-only prerequisite PR. It contains no product code, must be reviewed, green, and externally merged before any code PR, and must not be folded into an implementation PR.

PLANNED STACK (refine only to keep PRs reviewable):
0. Conditional prerequisite `docs(plan): reconcile M43 design` - scope: authoritative plan/prompt updates only; gate: reviewed, green, merged before the implementation stack.
1. PR-1 Objective-correctness verification - scope: strengthen verify_lesson so an accepted move is engine- or tablebase-confirmed against the objective, reusing persisted EngineAnalysis before invoking the engine; re-verify persisted lessons on read and mark rather than delete; commits: "feat(learning): verify that an accepted move answers the objective", "feat(learning): re-verify persisted lessons on read"; verification: `uv run pytest tests/unit/test_lesson_verification.py`
2. PR-2 Transfer position seeding (on PR-1) - scope: production seeder populating TransferPosition from the content catalog; commits: "feat(content): seed transfer positions from the catalog"; verification: `uv run pytest tests/integration/test_transfer_measurement.py -k seeding`
3. PR-3 Assignment and due selection (on PR-2) - scope: assign a transfer position on an active-skill mastery threshold and surface due transfer items inside the training session; commits: "feat(learning): assign transfer exercises at the mastery threshold", "feat(api): include due transfer items in a training session"; verification: `uv run pytest tests/integration/test_transfer_measurement.py -k assignment`
4. PR-4 Completion and report (on PR-3) - scope: record a measurement on completion and expose a per-player transfer report; commits: "feat(learning): record transfer measurements on completion", "feat(api): report transfer performance for a player"; verification: `uv run pytest tests/integration/test_transfer_measurement.py`

CONSTRAINTS: no scope leakage, minimal dependencies, repo style, no version/changelog updates. Do not touch the M30 controlled-study infrastructure. Never delete a lesson that fails re-verification - mark it. Do not assign transfer work from a retired taxonomy skill.
VERIFICATION (must pass): `uv run pytest tests/integration/test_transfer_measurement.py tests/unit/test_lesson_verification.py` passes; `scripts/check.sh` exits 0.
REVIEW:
Per PR:
- Scope matches its purpose; contracts match the reconciled plan; behavior is meaningfully tested.
- Re-verification marks rather than destroys; engine calls reuse persisted analysis where available.
- History is atomic, conventional, attribution-free, and free of unrelated formatting churn.
- PR-specific verification output is captured.
Whole stack:
- Bases form one valid stack; cumulative acceptance and integration hold; hosted CI is green on every PR; no regression coverage is removed without replacement.
- The docs-only root, when present, is reviewed and green before dependent code PRs.
- Report PR URLs, bases, verification, risks, manual gates, and review completion.
FINAL VERDICTS:
- Report the design verdict before the merge verdict.
- Then report exactly one merge verdict: `GO - RELEASE: unversioned - adaptive and operable - RELEASE PREP: pending` or `NO-GO - RELEASE: unversioned - adaptive and operable - REASON: <blocking gate>`.
- `GO` requires `DESIGN GO`, every PR correctly based/reviewed/green, local verification, and full milestone acceptance.
DONE: design verdict with evidence; when authorized, a reviewed stack with a release-aware merge verdict and evidence.
```

---

### M44 — Interaction-level e2e and a documented way to run the app

```text
/goal Deliver milestone M44 (Interaction-level e2e and a documented way to run the app) from DEVELOPMENT_PLAN.md as a reviewed stack of PRs.

CONTEXT: DEVELOPMENT_PLAN.md §6 Section I M44 + source .docs/2026-07-26-learning-loop-enhancement-plan.md §2.3 (G24) and §2.4 (G32, G33), .docs/scan64-system-design.md §22 (testing). Preconditions: M39 merged. Repo: Python 3.12+/uv backend; React 19 + TypeScript + Vite + pnpm frontend; Playwright; scripts/check.sh; hosted CI job "Quality".
M31 OWNERSHIP CONTRACT (H-002): the clean-clone walkthrough and e2e coverage must exercise PGN import with the active player identity, because an unowned import is rejected by design.
OBJECTIVE: The defect class that shipped a dead chessboard cannot ship again, and a new user can start the application from the README. Acceptance: an e2e spec fails when the board stops accepting pointer input, demonstrated by reverting the bounds recompute in a scratch worktree and observing the failure, then restoring; a clean clone reaches a playable board following only the README; setting SCAN64_DATABASE_URL relocates the database and the default preserves current behaviour; the window.__e2e_move hook remains only where pointer input is not the subject under test.
RELEASE TRAIN: target=unversioned - "adaptive and operable"; included milestones=M38-M44; preparation trigger=all included milestones externally merged; required artifacts=none (DEVELOPMENT_PLAN.md §2 GAP on release policy); release verification=scripts/check.sh exits 0 on main after the final merge plus this milestone's clean-clone quickstart walkthrough; publication=not requested. This is the final milestone of the train - report release preparation as due once it merges.

PRE-IMPLEMENTATION DESIGN GATE:
1. Read DEVELOPMENT_PLAN.md §6 M44, its §1 source-map rows, this prompt, and .docs/DEVELOPMENT_PLAN_HISTORY.md when present.
2. Inspect apps/scan64-web/tests/e2e/lesson.spec.ts, review-interaction.spec.ts:43-70, analysis-flow.spec.ts, offline-smoke.spec.ts:50-56, src/scan64/persistence/database.py:5-6, README.md, and M39's merged routing with its CI evidence.
3. Revalidate objective, interfaces, dependencies, acceptance, verification, risks, release train, and dependent milestones listed in M44's Design reevaluation row (none).
4. Append one ledger entry to .docs/DEVELOPMENT_PLAN_HISTORY.md: timestamp, milestone, decision, trigger, evidence, plan/prompt sections changed, downstream impact, implementation authorization.
5. If no material mismatch exists, report `DESIGN GO - PLAN REVISION: none`; this authorizes implementation.
6. If a mismatch exists, update both authoritative artifacts for M44 and every affected future milestone, append the revision ID, and report `DESIGN GO - PLAN REVISION: <entry IDs>`. This blocks product code until the reconciliation prerequisite merges.
7. If validity cannot be established, report `DESIGN NO-GO - REASON: <evidence>` and stop. After a reconciliation PR merges, repeat this gate and require `DESIGN GO - PLAN REVISION: none`.

RECONCILIATION RULE: A material revision opens `docs(plan): reconcile M44 design` as a docs-only prerequisite PR. It contains no product code, must be reviewed, green, and externally merged before any code PR, and must not be folded into an implementation PR.

PLANNED STACK (refine only to keep PRs reviewable):
0. Conditional prerequisite `docs(plan): reconcile M44 design` - scope: authoritative plan/prompt updates only; gate: reviewed, green, merged before the implementation stack.
1. PR-1 Pointer-driven play spec - scope: rewrite the play e2e to drive the board with real mouse events, waiting on board readiness rather than sleeping; commits: "test(web): play a move with real pointer input"; verification: `pnpm --dir apps/scan64-web test:e2e`
2. PR-2 Pointer-driven attempt and analysis specs (on PR-1) - scope: same treatment for lesson attempts and the analysis board; restrict window.__e2e_move to offline-queue specs where pointer input is not the subject; commits: "test(web): answer a lesson and inspect analysis with real pointer input", "refactor(web): limit the programmatic move hook to offline specs"; verification: `pnpm --dir apps/scan64-web test:e2e`
3. PR-3 Configurable database URL (on PR-2) - scope: SCAN64_DATABASE_URL with a default that reproduces today's resolution exactly; commits: "feat(persistence): allow the database location to be configured"; verification: `uv run pytest tests/unit -k database_url`
4. PR-4 Run script and README quickstart (on PR-3) - scope: scripts/run.sh starting API and web together; README quickstart naming uv, pnpm, and Stockfish prerequisites; commits: "build: add a script that runs the api and web together", "docs: document how to start scan64"; verification: clean-clone walkthrough following only the README

CONSTRAINTS: no scope leakage, minimal dependencies, repo style, no version/changelog updates. The database default must reproduce today's resolution exactly - a silent relocation of an existing local database is a defect. Do not add hosted deployment or container packaging.
VERIFICATION (must pass): `pnpm --dir apps/scan64-web test:e2e` passes; the mutation check - revert the play board's bounds recompute in a scratch git worktree, confirm the pointer spec fails, restore; `scripts/check.sh` exits 0; manual - clean-clone walkthrough following only the README.
REVIEW:
Per PR:
- Scope matches its purpose; contracts match the reconciled plan; behavior is meaningfully tested.
- Specs wait on readiness rather than sleeping; the mutation check is performed and its output captured, not asserted from reasoning.
- History is atomic, conventional, attribution-free, and free of unrelated formatting churn.
- PR-specific verification output is captured.
Whole stack:
- Bases form one valid stack; cumulative acceptance and integration hold; hosted CI is green on every PR; no regression coverage is removed without replacement.
- The docs-only root, when present, is reviewed and green before dependent code PRs.
- Report PR URLs, bases, verification, risks, manual gates, and review completion.
FINAL VERDICTS:
- Report the design verdict before the merge verdict.
- Then report exactly one merge verdict: `GO - RELEASE: unversioned - adaptive and operable - RELEASE PREP: pending` or `NO-GO - RELEASE: unversioned - adaptive and operable - REASON: <blocking gate>`.
- `GO` requires `DESIGN GO`, every PR correctly based/reviewed/green, local verification, and full milestone acceptance. On `GO`, note that this milestone completes the train and release preparation is now due.
DONE: design verdict with evidence; when authorized, a reviewed stack with a release-aware merge verdict and evidence.
```
