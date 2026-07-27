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
