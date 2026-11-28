# Review procedure

Every pull request is reviewed under this procedure. The review ends by setting the `scan64/review` commit status on the exact head commit it reviewed. The `main` and `epic/*` rulesets require that status, so nothing reaches either branch unreviewed. A pull request that touches a path in [`.github/CODEOWNERS`](../.github/CODEOWNERS) also needs an approval from a listed owner other than its author, and a pull request into `main` needs an approval as well. The merge rules are in [CONTRIBUTING.md rule 7](../CONTRIBUTING.md#rules).

A pull request stacked on a feature branch (its base is another pull request's branch) can merge without a review, because GitHub can't require checks there without also blocking pushes to working branches. Its commits are still reviewed before they land. The parent pull request's head then contains them, and the parent can't merge into the epic until a review sets `scan64/review` on that head. Reviewing the stacked pull request itself, before it merges, is cheaper when its parent will take a while.

This procedure comes from the reviews that found the defects CI passed in #391, #405, #418 and #422. Its required probes are the steps that found them.

## Who reviews

Any machine can run the review, the author's own included, under these conditions:

- **A fresh session.** The reviewer starts from the pull request number and this document. It has no access to the session, plan, notes or scratch files that produced the change, and it doesn't ask the authoring agent to explain the change. The code, the pull request body and the linked issues are its only inputs.
- **A different model where one is available.** Record the authoring and reviewing models in the review.
- **The reviewer never pushes to the pull request.** Fixes belong to the author.

The owner's reviews follow the same procedure.

## Steps

1. **Scope.** Read `gh pr view <N>` (base branch, head commit, commits, body) and `gh pr checks <N>`. Read the acceptance criteria of every issue in `Closes #…` (`gh issue view <M>`). Note whether the pull request is stacked: its base is another pull request's branch, or `git log <base>..<head>` contains another open pull request's commits.
2. **Check out** the head in a separate worktree (`git fetch origin pull/<N>/head:review/pr-<N>`, then `git worktree add ../Scan64-pr<N> review/pr-<N>`) and diff it against the base with `git diff <base>...HEAD`.
3. **Merge check.** Dry-run the merge into the current base head, and into any open pull request stacked under this one: `git merge-tree --write-tree --name-only <base> HEAD`. A conflict, or a branch built on an old copy of a pull request that has changed since, is a finding.
4. **Required probes.** Each one needs a command you ran and its result. Use the existing test fixtures, write scratch files outside the repository and delete them afterwards.

   | Probe | What it found before |
   |---|---|
   | Run each changed behaviour through the real component it depends on: the real app with its middleware stack, the real engine adapter with a UCI engine double, real IndexedDB in two pages. The pull request's own tests don't count when a fake stands in for the part that changed. | #391: a fake request object hid that cancellation never fires behind `BaseHTTPMiddleware` |
   | For each new error or exception type, find every caller of the code that raises it (`git grep`) and check that each one still catches it. Run one probe of the path that used to work on the base and on the head. | #405: a new `EngineCapabilityError` escaped the handler for `EngineError` and turned a skipped lesson into a 503 |
   | Name every group of players the change affects (with or without a local model, engines without a capability, desktop or web) and state what each one sees before and after, from a run on the base and on the head. | #391, #405 |
   | For benchmark or measurement changes: the run records its commit, uncommitted changes, configuration digest and engine binary and network digests; resuming checks the run's identity; the summary recomputes from the raw records ([guardrail 6](../CONTRIBUTING.md#saved-data-and-migrations)). | #418, #422 |
   | Check every acceptance criterion of the issues in `Closes #…`. An unmet one is a finding: finish it, or change `Closes` to `Refs` and narrow the issue. | #380 |

5. **Rules.** Check the change against the [engineering guardrails](../CONTRIBUTING.md#engineering-guardrails) (1–31) and the [rules for working across machines](../CONTRIBUTING.md#rules).
6. **Checks.** Run only checks that can fail ([rule 9](../CONTRIBUTING.md#rules)): the changed test files, Ruff on touched files, `mypy --strict` on touched `src/` files, and frontend checks only when `apps/scan64-web` changed. Read CI's results on the head commit (`gh api repos/Mathews-Tom/Scan64/commits/<head>/check-runs`) instead of rerunning what CI covers.
7. **Write the review** as one pull request comment in the format below. Write to the author in plain language. Severity is user impact.
   - **Blocking** means a demonstrated regression or a broken guardrail, with the probe that reproduces it. A problem that only appears after an operator changes configuration mid-session, and that is already documented as a limitation, is "Should fix", not "Blocking".
   - Every "Checked and fine" item names the probe or command and its result. A review without probes doesn't count.
   - Say when a claim rests on documentation rather than a run.
   - Never mention a person or bot with `@` outside a code span.
8. **Verdict.** Before posting, confirm the head hasn't moved (`gh pr view <N> --json headRefOid`). Post the comment with `gh pr comment <N> --body-file <file>`, which prints its URL, then set the status on the reviewed head:

   ```sh
   gh api repos/Mathews-Tom/Scan64/statuses/<head-sha> \
     -f context=scan64/review -f state=<success|failure> \
     -f description="<No blocking findings | N blocking findings> (procedure <short-sha>)" \
     -f target_url=<review comment URL>
   ```

   - No blocking findings: `success`. "Should fix" items don't block the merge. The author fixes them or answers them in the pull request before merging.
   - Blocking findings: `failure`. A reviewer who is not the pull request's GitHub author also submits a "Request changes" review (`gh pr review <N> --request-changes --body "See <comment URL>"`). GitHub doesn't let an author request changes on their own pull request, so the failed status is what blocks it.
9. **New commits.** A push changes the head commit, so it has no `scan64/review` status and can't merge until it's reviewed again.
   - **Pure rebase:** if `git range-diff <old-base>..<reviewed-head> <new-base>..<new-head>` shows no change to the pull request's own commits and step 3 passes, post a short comment saying so and set `success` with the description `Rebase of reviewed <short-sha>`.
   - **Otherwise:** review the new commits (`git diff <reviewed-head> <new-head>` when the old head is an ancestor) through the steps their changes touch, and check each earlier finding.
10. **Clean up.** As soon as the review is posted and the status is set, remove the worktree (`git worktree remove --force ../Scan64-pr<N>`), its local `review/*` branch and the scratch files. A review worktree lives for one review only. For a re-review, create a new one from the new head. Install frontend dependencies only when `apps/scan64-web` changed; they add about 300 MB to a worktree. `scan64-coord gc --apply` removes any finished worktree that was missed, and the session-start hook runs it automatically.

Large pull requests can be split into slices with one reviewer per slice. Each reviewer reproduces its own blocking claims, and one combined comment sets the status.

## Review comment format

```text
## Review under docs/review-procedure.md

### Blocking
<finding, file:line, the players affected and what they see, the probe and its output; or "None">

### Should fix
### Issues closed but not fully met
### Suggestions
### Checks run
<command: result; and what was left to CI>

### Checked and fine
<item: probe or command, result>

---
Scan64-Review: <commit of docs/review-procedure.md used>
Reviewed-head: <head sha>
Verdict: <pass | blocking>
Reviewer: <machine>, <reviewing model>; author model: <model>; fresh session: yes
```

## Audit

The owner reviews the first five pull requests reviewed under this procedure independently and compares the findings. After that, the owner reviews about one in four, and every pull request that touches a CODEOWNERS path needs the owner's approval anyway. When the owner finds a blocking defect that a procedure review missed, it goes into a rule-change issue. Until the owner decides that issue, the owner can make the affected paths need an approval by adding them to CODEOWNERS.

## Changing this procedure

Only the owner changes this document. To propose a change, open a [rule-change issue](https://github.com/Mathews-Tom/Scan64/issues/new?template=rule-change.yml) ([CONTRIBUTING.md, "Changing the rules"](../CONTRIBUTING.md#changing-the-rules)).
