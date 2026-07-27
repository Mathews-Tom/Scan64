# Development Plan History — Scan64

**This is a committed, append-only design-evidence ledger.** `.docs/DEVELOPMENT_PLAN.md` and `.docs/EXECUTION_PROMPTS.md` remain the authoritative planning artifacts; this ledger records their design-gate provenance and must remain consistent with them.

Append one entry per pre-implementation design gate. Never rewrite an existing entry — supersede it with a new one.

## Entry format

| Field | Content |
| --- | --- |
| ID | `H-<NNN>` |
| Timestamp | ISO-8601 UTC |
| Milestone | `M<NN>` |
| Decision | `DESIGN GO — PLAN REVISION: none` \| `DESIGN GO — PLAN REVISION: <entry IDs>` \| `DESIGN NO-GO — REASON: <evidence>` |
| Trigger | What prompted the gate (scheduled milestone start, predecessor merge, reconciliation re-run) |
| Evidence | Files, line ranges, commits, PR numbers, CI runs, and command output inspected |
| Plan/prompt sections changed | Exact sections in both authoritative files, or `none` |
| Downstream impact | Dependent milestone IDs reviewed, and whether each changed |
| Implementation authorization | `authorized` \| `blocked pending <reconciliation PR>` |

---

## H-001

| Field | Content |
| --- | --- |
| ID | `H-001` |
| Timestamp | 2026-07-26T16:30:00Z |
| Milestone | `plan` (not a milestone gate — plan regeneration record) |
| Decision | `DESIGN GO — PLAN REVISION: H-001` |
| Trigger | Regeneration requested against `.docs/2026-07-26-learning-loop-enhancement-plan.md` after M1–M30 merged and a live audit found the play → analysis → profile loop unwired. |
| Evidence | `main` @ `6da18dd`; `scripts/check.sh` green; PR #119 merged with hosted `Quality` check passing in 2m18s. Live-stack audit: `GET /v1/games/{id}/positions` returned `[]` after a `completed` analysis job; `database.db` inspection showed 18 games, 0 positions, 0 evidence rows, 20 skill states all originating from famous-game attempts; a hanging-queen PGN produced one `board_awareness.hanging_piece` lesson whose explanation was the fixed fallback string. Two read-only source sweeps produced the G1–G33 inventory with `file:line` evidence against `f616763`. Repo evidence: `pyproject.toml:3` `version = "0.1.0"`, no `CHANGELOG.md`, zero tags, `.github/workflows/ci.yml` job `Quality`, branch ruleset on `main` (`pull_request`, `non_fast_forward`, `deletion`). |
| Plan/prompt sections changed | `.docs/DEVELOPMENT_PLAN.md` — full regeneration for M31–M44 (§1–§8). `.docs/EXECUTION_PROMPTS.md` — full regeneration, one `/goal` block per milestone. Prior M1–M30 artifacts were preserved verbatim at `.docs/DEVELOPMENT_PLAN_M1-M30_DELIVERED.md` and `.docs/EXECUTION_PROMPTS_M1-M30_DELIVERED.md`; H-005 corrects this entry's earlier untracked-artifact claim. |
| Downstream impact | Not applicable — no milestone in this plan has begun. Two corrections carried forward from the delivered plan: hosted CI is live (the delivered plan's §2 claim that it is disabled is stale), and `main` is protected by a ruleset requiring pull requests. |
| Implementation authorization | `authorized` for M31's design gate. No product code authorized by this entry. |

## H-002

| Field | Content |
| --- | --- |
| ID | `H-002` |
| Timestamp | 2026-07-27T00:10:55Z |
| Milestone | `M31` |
| Decision | `DESIGN GO — PLAN REVISION: H-002` |
| Trigger | M31 root-milestone pre-implementation design gate on `main` at `bf35919f`. |
| Evidence | `.docs/DEVELOPMENT_PLAN.md` §1, §2, §4, §6 M31 and dependent M32/M33/M34/M36/M39/M41/M42/M44 rows; `.docs/EXECUTION_PROMPTS.md` M31; enhancement plan §2.1 G1–G3; system design §7 and §17; `src/scan64/chess/analysis/jobs.py:49-138`, `api/games.py:20-63,200-222`, `api/reports.py:72-113`, `Game`/`Position`/`EngineAnalysis`/`Evidence` models, and `PgnImportScreen.tsx:29-83`. G1–G3 still hold: analysis hardcodes `system`, persists no Position/EngineAnalysis/Evidence, and evidence reads through positions. Material mismatch: import has no player input or durable owner; `Game` has no player field; the import UI creates only a Game; the plan's proposed PlaySession lookup cannot attribute an imported game. System design §17.3 also defines retention semantics, contrary to the plan's claim that none exist. |
| Plan/prompt sections changed | `.docs/DEVELOPMENT_PLAN.md` §2, M31, M32, M33, M34, M36, M39, M41, M42, M44. `.docs/EXECUTION_PROMPTS.md` M31 and the matching affected future milestone contracts. |
| Downstream impact | M32, M33, M34, M36, M39, M41, M42, and M44 revised to consume `Game.owner_player_id`; M35, M37, M38, M40, M43 remain behaviorally unchanged but require their normal design reevaluation against the merged M31 contract. |
| Implementation authorization | `blocked pending docs(plan): reconcile M31 design` reviewed, green, and externally merged. |

## H-003

| Field | Content |
| --- | --- |
| ID | `H-003` |
| Timestamp | 2026-07-27T00:23:02Z |
| Milestone | `M31` |
| Decision | `DESIGN GO — PLAN REVISION: H-002, H-003` |
| Trigger | Review remediation for docs-only reconciliation PR #121. |
| Evidence | Internal review of #121 identified missing schema-migration mechanism, unscheduled breaking PGN import caller work, an impossible ownerless-row assignment promise, and an M40 empty-state omission. Repository inspection confirms `SQLModel.metadata.create_all()` is the only schema path and cannot alter tables; source design §17.3 requires Alembic migrations. |
| Plan/prompt sections changed | `.docs/DEVELOPMENT_PLAN.md` §1, §2, M31, M39, M40. `.docs/EXECUTION_PROMPTS.md` global provenance, M31 stack and verification, M40 contract. |
| Downstream impact | M31 grows to five implementation PRs: migration bootstrap, player-owned import plus attribution, positions/analysis, evidence, and coverage. M39 and M40 now define ownerless legacy behavior; M32/M33/M34/M36/M41/M42/M44 retain the H-002 ownership propagation. |
| Implementation authorization | `blocked pending amended docs(plan): reconcile M31 design` reviewed, green, and externally merged. |

## H-004

| Field | Content |
| --- | --- |
| ID | `H-004` |
| Timestamp | 2026-07-27T00:36:04Z |
| Milestone | `M31` |
| Decision | `DESIGN GO — PLAN REVISION: none` |
| Trigger | Required post-reconciliation re-run after PR #121 merged to `main`. |
| Evidence | `main` at `cc2922131f48961c04a6d4a3ce8a74fbe30f818d`; PR #121 externally merged 2026-07-27T00:35:51Z with hosted `Quality` run 30227672514 passed in 2m24s. The merged plan/prompt define H-002 owner attribution and H-003 Alembic migration bootstrap. The M31 source evidence remains current: `jobs.py:49-138` still hardcodes `system` and drops Position/EngineAnalysis/Evidence; `api/games.py:20-63` and `PgnImportScreen.tsx:29-83` still lack ownership; `api/reports.py:72-113` still reads evidence through positions. No product-code change occurred between reconciliation and this re-run. Plan/prompt contract, DAG, release-train, gap-coverage, and Mermaid validation passed; focused review approved after the H-002/H-003 traceability correction. |
| Plan/prompt sections changed | `none` |
| Downstream impact | M32, M33, M34, M40, M41, M42, and transitive M35, M36, M37, M38, M39, M43, M44 retain the merged H-002/H-003 contracts and require normal design reevaluation after M31 lands. |
| Implementation authorization | `authorized` |

## H-005

| Field | Content |
| --- | --- |
| ID | `H-005` |
| Timestamp | 2026-07-27T07:10:00Z |
| Milestone | `M31` |
| Decision | `DESIGN GO — PLAN REVISION: H-005` |
| Trigger | Final M31 review found that preserving legacy evidence requires a one-time owner backfill from an existing `PlaySession`, which contradicted H-002/H-003's null-backfill wording. |
| Evidence | Review of M31 migration `20260727_02_game_ownership.py`, owner-based evidence query, and legacy SQLite test found that leaving every pre-M31 game ownerless makes existing player evidence unreachable. The migration can safely derive an owner only where an existing `PlaySession` supplies one; games without that record remain ownerless and rejected before analysis. |
| Plan/prompt sections changed | `.docs/DEVELOPMENT_PLAN.md` H-002/H-003 and M31 scope, deliverables, acceptance, verification, and risks; `.docs/EXECUTION_PROMPTS.md` M31 ownership/migration contract, gate read set, PR-2 scope/verification, and constraints. |
| Downstream impact | M32, M33, M34, M40, M41, M42, and transitive M35, M36, M37, M38, M39, M43, M44 retain `Game.owner_player_id` as their ongoing ownership boundary; the migration-only provenance clarification does not change their implementation scopes. |
| Implementation authorization | `blocked pending this docs(plan): reconcile M31 backfill review, green hosted Quality, and external merge; rerun the M31 gate after merge before landing product PRs.` |

## H-006

| Field | Content |
| --- | --- |
| ID | `H-006` |
| Timestamp | 2026-07-27T01:52:41Z |
| Milestone | `M31` |
| Decision | `DESIGN GO — PLAN REVISION: none` |
| Trigger | Required post-reconciliation re-run after PR #127 merged to `main`. |
| Evidence | `main` at `60cc968c161c935db253b550372b22a01025d340`; PR #127 externally merged 2026-07-27T01:51:56Z with hosted Quality run 30230670357 passed in 2m26s. H-005 reconciles the migration-only `PlaySession` backfill without changing ongoing attribution: `Game.owner_player_id` remains the player-owned read boundary, session-less legacy games remain ownerless, and M31's dependent contracts remain compatible. |
| Plan/prompt sections changed | `none` |
| Downstream impact | M32, M33, M34, M40, M41, M42, and transitive M35, M36, M37, M38, M39, M43, M44 retain H-005's clarified ownership contract and require normal design reevaluation after M31 merges. |
| Implementation authorization | `authorized` |

## H-007

| Field | Content |
| --- | --- |
| ID | `H-007` |
| Timestamp | 2026-07-27T02:28:40Z |
| Milestone | `M32` |
| Decision | `DESIGN GO — PLAN REVISION: none` |
| Trigger | M32 pre-implementation design gate after every M31 PR merged to `main`. |
| Evidence | `main` at `671c323c40068f5fccab73a8978622ae710387db`. M31 stack merged externally: PRs #122, #129, #124, #125, #126, each with a green hosted `CI`/`Quality` run (latest runs 30231425896, 30231564249, 30231692446, all `success`). Read: `.docs/DEVELOPMENT_PLAN.md` §1, §2, §4, §6 M32 and dependent M34/M39/M41 rows; `.docs/EXECUTION_PROMPTS.md` M32, M34, M39, M41; enhancement plan §2.1 G4/G9; system design §7. Inspected `src/scan64/chess/games/play_session_service.py:65-154`, `src/scan64/api/play.py:1-139`, `src/scan64/api/games.py:39-178`, `src/scan64/api/players.py`, `src/scan64/api/reports.py:112-119`, `src/scan64/chess/analysis/jobs.py:73-218`, `src/scan64/chess/analysis/models.py`, `src/scan64/chess/analysis/admission.py`, `src/scan64/chess/games/models.py`, and `src/scan64/persistence/migrations/versions/`. G4 and G9 still hold: a played game is never analysed, `Game.pgn` stays `""` and `white`/`black` stay the `"Player"`/`"Opponent"` literals, no resign transition exists, and no player-scoped games endpoint exists. M31's ownership contract is live and satisfied ahead of schedule for play: `play_session_service.py:78-84` and `api/play.py:86-95` already write `Game.owner_player_id` from the session player, so M32 PR-1 narrows to the `white`/`black` literals; this satisfies the planned contract rather than contradicting it. Terminal state currently has two call sites (`play_session_service.py:110-116` and `:142-144`), which the planned single transition point consolidates. No new schema column is required, so no Alembic revision is added. Player-scoped reads consistently require `require_player_token` (`players.py:73-80`, `reports.py:62-135`, `data_lifecycle.py:249-256`), so `GET /v1/players/{id}/games` adopts the same guard, and it returns per-game `diagnosis_count` and `date` because M39's plan row requires result, date, and diagnosis count from this endpoint — a response-field detail inside M32's stated scope, not a contract change. |
| Plan/prompt sections changed | `none` |
| Downstream impact | M34 reviewed — unchanged; it consumes `PersistedLessonOpportunity.player_id`, which already derives from `Game.owner_player_id` and is unaffected by play attribution. M39 reviewed — unchanged; its games list is served by `GET /v1/players/{id}/games` with result, date, and diagnosis count, and must send the player token. M41 reviewed — unchanged; it removes M32's interim in-flight cap, which is deliberately a single module (`chess/analysis/inflight.py`) with one submission call site so removal stays a one-place change and no second quota system is introduced. Transitive M36, M37, M38, M43, M44 reviewed — no interface they depend on changes. |
| Implementation authorization | `authorized` |


## H-008

| Field | Content |
| --- | --- |
| ID | `H-008` |
| Timestamp | 2026-07-27T04:57:04Z |
| Milestone | `M33` |
| Decision | `DESIGN GO — PLAN REVISION: H-008` |
| Trigger | M33 pre-implementation design gate after M31's merged persistence stack and M32's merged play-analysis path. |
| Evidence | `main` at `db088ccf47cf6f39a003ea4809273203528f5469`; M31 PRs #122, #129, #124, #125, and #126 are merged and each has a successful hosted `Quality` check. `run_analysis_for_game` derives `PlayerContext` from `Game.owner_player_id`, persists only hanging-piece evidence, concretely constructs `HangingPieceDetector`, and never calls `FocusedPassOrchestrator`. The registry has no host lifecycle binding. `benchmarks/fixtures/golden_corpus.json` contains benchmark-only `mock_evidence`, no games, moves, fast candidates, or focused responses, while `diagnosis_report.py` alone constructs all ten detectors. Therefore the prior M33 production-path coverage acceptance could not be validly implemented or measured. M34 remains compatible with M31 attribution; M35 needed the emitted evidence contract clarification; M36, M37, M38, and M43 have no changed interface. |
| Plan/prompt sections changed | `.docs/DEVELOPMENT_PLAN.md` M33 scope, deliverables, acceptance, verification, reevaluation, risks, and M35 evidence contract; `.docs/EXECUTION_PROMPTS.md` M33 objective, PR-3, PR-4, PR-5, constraints, verification, and M35 preconditions, design read set, and PR-1/PR-2 scopes. |
| Downstream impact | M34 reviewed — unchanged because it consumes player-attributed opportunities. M35 now consumes M33's provenance-bearing, code-specific evidence payload contract and must fail on absent required fields. M36, M37, M38, and M43 reviewed — unchanged; their existing reevaluation gates remain required. |
| Implementation authorization | `blocked pending docs(plan): reconcile M33 design` reviewed, green hosted `Quality`, and externally merged; reload both authoritative artifacts and rerun the M33 design gate before product code. |

## H-009

| Field | Content |
| --- | --- |
| ID | `H-009` |
| Timestamp | 2026-07-27T05:13:01Z |
| Milestone | `M33` |
| Decision | `DESIGN GO — PLAN REVISION: none` |
| Trigger | Required post-reconciliation re-run after PR #136 merged to `main`. |
| Evidence | `main` at `51347daf77e78c0c59486c34d02f83202e6cfddb`; PR #136 is externally merged with successful hosted `Quality` run 30238950712. Re-read the reconciled M33 and M35 plan/prompt contracts, H-008, source G6/G7, system design §§7.2–7.4 and §8.10, M31 PR evidence, `jobs.py`, `orchestration.py`, `registry.py`, `diagnosis_report.py`, and the `Diagnosis` contract. The reconciled production fixture, evidence-provenance, primary/secondary, precision, ownership, focused-pass, and M35 payload interfaces are mutually compatible. M31's ongoing `Game.owner_player_id` attribution remains intact. |
| Plan/prompt sections changed | `none` |
| Downstream impact | M34 reviewed — unchanged; it consumes player-attributed opportunities. M35 reviewed — unchanged from H-008 and consumes the established provenance-bearing evidence contract. M36, M37, M38, and M43 reviewed — unchanged; their normal design reevaluation gates remain required. |
| Implementation authorization | `authorized` |

## H-010

| Field | Content |
| --- | --- |
| ID | `H-010` |
| Timestamp | 2026-07-27T11:56:54Z |
| Milestone | `M34` |
| Decision | `DESIGN GO — PLAN REVISION: H-010` |
| Trigger | M34 pre-implementation design gate after M32 and M33 merged to `main`. |
| Evidence | `main` at `f9a0169666801d51d5894900ac74503ec52d117e`; M32 PRs #131–#135 and M33 PRs #138–#142 are externally merged, each with a successful hosted `Quality` check; `main` CI run 30243077545 is successful. Re-read the M34 source map, enhancement G5/G14/G15/G31, system design §§8.10, 9, and 21, H-007–H-009, `content/tracking.py`, `profiling/priors.py`, `api/learning.py`, `analysis/jobs.py`, `SkillState`, `ReviewSchedule`, and taxonomy migration. G5, G14, G15, and G31 remain valid and analysis has the required durable owner-derived player id. Material mismatch: `ReviewSchedule` stores only an arbitrary lesson `item_id`, while `SkillState` has no retirement metadata; the existing migration therefore cannot remap a schedule by taxonomy skill or retain an unmappable live row with a reason. |
| Plan/prompt sections changed | `.docs/DEVELOPMENT_PLAN.md` §2, M34, M36, M37, M38, M42, and M43; `.docs/EXECUTION_PROMPTS.md` M34, M36, M37, M38, M42, and M43. |
| Downstream impact | M34 now persists the non-null four-part observation key, stores `ReviewSchedule.skill_id`, retains retirement metadata on both live row types, applies a deterministic target-key collision merge, and ships a schema upgrade. M36 excludes retired rows from current mastery fields; M37 records valid retired-code attempts while explicitly skipping profile mutation; M38 selects active state and non-retired schedules; M42 includes `ProfileObservation` in the lifecycle contract; M43 assigns transfer work only from active concepts. |
| Implementation authorization | `blocked pending docs(plan): reconcile M34 design` reviewed, green hosted `Quality`, and externally merged; rerun the M34 design gate after merge before product code. |

## H-011

| Field | Content |
| --- | --- |
| ID | `H-011` |
| Timestamp | 2026-07-27T12:22:19Z |
| Milestone | `M34` |
| Decision | `DESIGN GO — PLAN REVISION: none` |
| Trigger | Required post-reconciliation re-run after PR #143 merged to `main`. |
| Evidence | `main` at `0270d857c72b60a4bc42628463ebb14f942a835c`; PR #143 is externally merged with successful hosted `Quality` run 30265396073. Re-read the reconciled M34 and downstream M36/M37/M38/M42/M43 plan and prompt contracts, H-010, source G5/G14/G15/G31, system design §§8.10, 9, and 21, M32/M33 PR and CI evidence, `content/tracking.py`, `profiling/priors.py`, `api/learning.py`, `analysis/jobs.py`, `SkillState`, `ReviewSchedule`, the Alembic chain, and taxonomy migration. The four-part non-null observation contract, schema upgrade, target-key collision merge, retained retirement contract, lifecycle edge, and downstream readers are mutually compatible. M31's owner-derived player attribution remains intact. |
| Plan/prompt sections changed | `none` |
| Downstream impact | M36, M37, M38, M42, and M43 retain the reconciled active/retired and observation-lifecycle contracts. The implementation stack may now start from `main`. |
| Implementation authorization | `authorized` |

## H-012

| Field | Content |
| --- | --- |
| ID | `H-012` |
| Timestamp | 2026-07-27T18:45:00Z |
| Milestone | `M34` |
| Decision | `DESIGN GO — PLAN REVISION: H-012` |
| Trigger | M34 implementation-stack review found that review schedules were keyed by persisted analysis-lesson ids while the only M34 attempt path used famous-game content-item ids, and that a taxonomy rename could bypass the four-part observation idempotency key. |
| Evidence | Review of final M34 heads #145–#150 confirmed the schedule writer stores canonical `str(PersistedLessonOpportunity.id)`, `api/content.py` can only submit a `ContentItem.id`, and M37 is the first planned generic lesson-attempt endpoint. The review also established that remapping only `SkillState` and `ReviewSchedule` lets a renamed diagnosis miss its old `ProfileObservation` idempotency row on re-analysis, and that `ProfileObservation` needs retirement metadata to retain unmappable and collided records without deleting a key. |
| Plan/prompt sections changed | `.docs/DEVELOPMENT_PLAN.md` M34 and M37; `.docs/EXECUTION_PROMPTS.md` M34 and M37. |
| Downstream impact | M34 writes schedules only for persisted analysis lessons, remaps active `ProfileObservation` identities with the live taxonomy rows, and retains unmappable or collision-superseded observations with a reason. M37 owns advancing those schedules after resolving an owned persisted lesson through its generic endpoint; G15's historical `api/content.py` pointer is closed because content items cannot identify an M34 schedule. M36, M38, M42, and M43 retain their active/retired and lifecycle contracts; their design gates must verify canonicalized observation identity and owned lesson resolution. |
| Implementation authorization | `blocked pending docs(plan): reconcile M34 design` reviewed, green hosted `Quality`, and externally merged; rerun the M34 design gate after merge before product-code remediation. |

## H-013

| Field | Content |
| --- | --- |
| ID | `H-013` |
| Timestamp | 2026-07-27T19:05:00Z |
| Milestone | `M34` |
| Decision | `DESIGN GO — PLAN REVISION: none` |
| Trigger | Required post-reconciliation re-run after PR #151 merged to `main`. |
| Evidence | `main` at `a7ab0e6150a100c9e56a01734f198c6bfed51cda`; PR #151 is externally merged with successful hosted `Quality` run 30271056160. Re-read the reconciled M34/M37 contracts, H-012, source G5/G14/G15/G31, system design §§8.10, 9, and 21, final M34 stack review findings, `content/tracking.py`, `profiling/priors.py`, `api/learning.py`, `analysis/jobs.py`, `SkillState`, `ReviewSchedule`, `ProfileObservation`, the Alembic chain, and taxonomy migration. The canonical persisted-lesson schedule identity, M37-only advancement boundary, observation retention/collision rule, owner-derived identity, and downstream active/retired contracts are mutually compatible. |
| Plan/prompt sections changed | `none` |
| Downstream impact | M36, M37, M38, M42, and M43 retain the reconciled contracts. The M34 remediation stack may now rebase onto `main`, add its missing acceptance coverage and migration rules, then be re-reviewed before merge. |
| Implementation authorization | `authorized` |
