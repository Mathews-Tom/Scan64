#!/usr/bin/env python3
"""scan64-coord: claim Scan64 tickets per machine so parallel machines never collide.

A claim is the `machine:<name>` label on the GitHub issue or PR. The Scan64
board (Status, Machine) mirrors it. The rules are in CONTRIBUTING.md, section
"Working across machines". Every machine runs the same commands:

  scan64-coord status              what every machine holds, what waits on this machine
  scan64-coord claim N [--takeover]
                                   claim issue or PR N for this machine
  scan64-coord split N TITLE       new sub-issue for this machine's share of N, claimed here
  scan64-coord release N [--review|--done]
                                   --review keeps the claim and labels the ticket's open PRs;
                                   --done marks it merged; no flag gives the ticket back
  scan64-coord ask N MACHINE TEXT  ask MACHINE to act on issue/PR N: comment + needs:MACHINE
  scan64-coord answer N TEXT       reply on N and clear needs:<this machine>
  scan64-coord check [BRANCH]      exit 1 if BRANCH's ticket belongs to another machine
  scan64-coord setup MACHINE       one-time setup: machine name, installed copy, command
  scan64-coord gc [--apply]        remove finished worktrees: clean, on origin, and merged,
                                   closed, moved past or reviewed (dry run without --apply)
  scan64-coord install-hooks       add the Claude Code hooks to ~/.claude/settings.json
  scan64-coord hook-session        SessionStart hook: refresh the tool, remove finished
                                   worktrees, print the status
  scan64-coord hook-pretool        PreToolUse hook: deny git push / gh pr create on a conflict

Machine names are the repository's `machine:<name>` labels. The part of a name
before the first "-" is its OS, which matches the `needs-test:<os>` labels. A
`needs:<machine>` label marks an issue or PR that waits on that machine; the
request is the latest comment that starts with "For **<machine>**".
This machine's name comes from $SCAN64_MACHINE or ~/.config/scan64/machine.
Needs the `gh` CLI, logged in.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO = "Mathews-Tom/Scan64"
BOARD_OWNER = "Mathews-Tom"
BOARD_NUMBER = "1"
RULES = "CONTRIBUTING.md, section 'Working across machines'"
# hook-session refreshes the installed copy from the first of these refs, in the
# checkout setup ran in, that has the tool. While epic/371 is open the tool is
# only there; once it reaches main, main wins and the epic ref may disappear.
TOOL_REFS = ("origin/main", "origin/epic/371")
TOOL_PATH = "scripts/scan64_coord.py"
# Not ~/.local/share/scan64-coord: that is where the earlier standalone repo was cloned.
INSTALL_DIR = Path.home() / ".local" / "share" / "scan64" / "coord"
MACHINE_FILE = Path.home() / ".config" / "scan64" / "machine"
CLAUDE_SETTINGS = Path.home() / ".claude" / "settings.json"
_MACHINE_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_PUSH = re.compile(r"\bgit\b.*\bpush\b|\bgh\s+pr\s+create\b")


def gh(*args: str, check: bool = True, timeout: float | None = None) -> str:
    result = subprocess.run(
        ["gh", *args], capture_output=True, text=True, encoding="utf-8", timeout=timeout
    )
    if check and result.returncode != 0:
        raise SystemExit(f"gh {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


# --- Pure helpers ------------------------------------------------------------------


def os_of(machine_name: str) -> str:
    """The OS part of a machine name: `linux-5090` -> `linux`, `mac` -> `mac`."""
    return machine_name.split("-", 1)[0]


def ticket_in_branch(branch: str) -> str | None:
    """The issue number in a `<type>/<issue>-<slug>` branch name."""
    match = re.search(r"/(\d{2,5})(?:-|$)", branch)
    return match.group(1) if match else None


def is_push_command(command: str) -> bool:
    return bool(_PUSH.search(command))


def merge_hooks(settings: dict[str, Any], base: str) -> list[str]:
    """Add the SessionStart and PreToolUse(Bash) hooks to Claude Code settings in place.

    Other hooks stay. A hook that already runs a scan64_coord.py, including one from
    the earlier standalone scan64-coord repo, is pointed at ``base`` instead of added
    twice. Returns one message per event.
    """
    hooks = settings.setdefault("hooks", {})
    wanted = {
        "SessionStart": ("", f"{base} hook-session"),
        "PreToolUse": ("Bash", f"{base} hook-pretool"),
    }
    messages = []
    for event, (matcher, command) in wanted.items():
        entries = hooks.setdefault(event, [])
        existing = [
            hook
            for entry in entries
            for hook in entry.get("hooks", [])
            if "scan64_coord.py" in hook.get("command", "")
        ]
        if existing:
            changed = any(hook["command"] != command for hook in existing)
            for hook in existing:
                hook["command"] = command
            messages.append(f"{event}: {'updated' if changed else 'already installed'}")
            continue
        entry = next((e for e in entries if e.get("matcher", "") == matcher), None)
        if entry is None:
            entry = {"matcher": matcher, "hooks": []}
            entries.append(entry)
        entry["hooks"].append({"type": "command", "command": command, "timeout": 30})
        messages.append(f"{event}: installed")
    return messages


# --- GitHub state ------------------------------------------------------------------


def machines() -> list[str]:
    out = gh(
        "label", "list", "-R", REPO, "--search", "machine:", "--limit", "100", "--json", "name"
    )
    return sorted(
        label["name"].split(":", 1)[1]
        for label in json.loads(out)
        if label["name"].startswith("machine:")
    )


def machine() -> str:
    name = os.environ.get("SCAN64_MACHINE", "").strip()
    if not name and MACHINE_FILE.exists():
        name = MACHINE_FILE.read_text(encoding="utf-8").strip()
    if not _MACHINE_NAME.match(name):
        raise SystemExit(
            f"Unknown machine {name!r}. Run `scan64-coord setup <machine>` or set SCAN64_MACHINE."
        )
    return name


def ticket(number: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(
        gh("issue", "view", number, "-R", REPO, "--json", "number,title,labels,state,url")
    )
    data["machines"] = [
        label["name"].split(":", 1)[1]
        for label in data["labels"]
        if label["name"].startswith("machine:")
    ]
    return data


def board_items() -> list[dict[str, Any]]:
    out = gh(
        "project",
        "item-list",
        BOARD_NUMBER,
        "--owner",
        BOARD_OWNER,
        "--format",
        "json",
        "--limit",
        "500",
    )
    items: list[dict[str, Any]] = json.loads(out)["items"]
    return items


def board_set(number: int, status: str | None, machine_name: str | None) -> None:
    """Mirror a claim on the board; add the item if missing. Board errors never block."""
    try:
        project = json.loads(
            gh("project", "view", BOARD_NUMBER, "--owner", BOARD_OWNER, "--format", "json")
        )
        fields = json.loads(
            gh("project", "field-list", BOARD_NUMBER, "--owner", BOARD_OWNER, "--format", "json")
        )["fields"]
        by_name = {field["name"]: field for field in fields}
        item = next(
            (i for i in board_items() if i.get("content", {}).get("number") == number), None
        )
        if item is None:
            kind = "pull" if gh("pr", "view", str(number), "-R", REPO, check=False) else "issues"
            item = json.loads(
                gh(
                    "project",
                    "item-add",
                    BOARD_NUMBER,
                    "--owner",
                    BOARD_OWNER,
                    "--url",
                    f"https://github.com/{REPO}/{kind}/{number}",
                    "--format",
                    "json",
                )
            )
        for field, value in (("Status", status), ("Machine", machine_name)):
            if value is None:
                continue
            option = next(o["id"] for o in by_name[field]["options"] if o["name"] == value)
            gh(
                "project",
                "item-edit",
                "--project-id",
                project["id"],
                "--id",
                item["id"],
                "--field-id",
                by_name[field]["id"],
                "--single-select-option-id",
                option,
            )
    except (SystemExit, StopIteration, KeyError) as error:
        print(f"warning: board not updated: {error}", file=sys.stderr)


# --- Commands ----------------------------------------------------------------------


def cmd_claim(number: str, takeover: bool) -> int:
    me = machine()
    data = ticket(number)
    others = [m for m in data["machines"] if m != me]
    if others and not takeover:
        print(
            f"#{number} is claimed by {', '.join(others)}. Not claimed. "
            "Pick another ticket, or agree a handover and rerun with --takeover."
        )
        return 1
    for other in others:
        gh("issue", "edit", number, "-R", REPO, "--remove-label", f"machine:{other}")
    gh("issue", "edit", number, "-R", REPO, "--add-label", f"machine:{me}")
    note = f" (taken over from {', '.join(others)})" if others else ""
    gh(
        "issue",
        "comment",
        number,
        "-R",
        REPO,
        "--body",
        f"Claimed by **{me}**{note} via scan64-coord. Other machines: do not push to this ticket.",
    )
    board_set(int(number), "In progress", me)
    print(f"Claimed #{number} for {me}{note}: {data['title']}")
    return 0


def cmd_split(number: str, title: str) -> int:
    """Create a sub-issue for this machine's share of a ticket another machine owns."""
    me = machine()
    parent = ticket(number)
    body = (
        f"Part of #{number} ({parent['title']}): the **{me}** share of the work.\n\n"
        f"Created by scan64-coord split. #{number} stays with its owner; this issue holds "
        "this machine's work and results. Branch names use this issue's number."
    )
    url = gh(
        "issue", "create", "-R", REPO, "-t", f"{title} (part of #{number})", "--body", body
    ).strip()
    child = url.rsplit("/", 1)[-1]
    gh(
        "issue",
        "comment",
        number,
        "-R",
        REPO,
        "--body",
        f"Split: **{me}** takes #{child} ({title}) for its share of this ticket.",
    )
    print(f"Created #{child} for {me}.")
    return cmd_claim(child, takeover=False)


def cmd_release(number: str, status: str) -> int:
    me = machine()
    if status != "In review":  # a PR in review stays owned by its machine
        gh("issue", "edit", number, "-R", REPO, "--remove-label", f"machine:{me}")
    gh(
        "issue",
        "comment",
        number,
        "-R",
        REPO,
        "--body",
        f"Status set by **{me}** via scan64-coord: {status}.",
    )
    board_set(int(number), status, None)
    if status == "In review":
        # Label the open PRs whose branch names this ticket, so each PR shows its
        # owning machine and is protected like the issue.
        prs = json.loads(
            gh(
                "pr",
                "list",
                "-R",
                REPO,
                "--state",
                "open",
                "--limit",
                "100",
                "--json",
                "number,headRefName",
            )
        )
        for pr in prs:
            if ticket_in_branch(pr["headRefName"]) == number:
                gh("pr", "edit", str(pr["number"]), "-R", REPO, "--add-label", f"machine:{me}")
                board_set(pr["number"], "In review", me)
                print(f"Labelled PR #{pr['number']} ({pr['headRefName']}) for {me}")
    print(f"Released #{number} ({status})")
    return 0


def search_open(label: str) -> list[dict[str, Any]]:
    """Open issues and PRs in REPO with ``label``, straight from GitHub (not the board)."""
    out = gh(
        "api",
        "-X",
        "GET",
        "search/issues",
        "-f",
        f'q=repo:{REPO} is:open label:"{label}"',
        "-f",
        "per_page=100",
    )
    items: list[dict[str, Any]] = json.loads(out)["items"]
    return items


def request_comment(sender: str, target: str, text: str) -> str:
    return f"For **{target}** (from {sender}): {text}"


def cmd_ask(number: str, target: str, text: str) -> int:
    me = machine()
    if target not in machines():
        print(f"No machine:{target} label in {REPO}.")
        return 2
    gh("issue", "comment", number, "-R", REPO, "--body", request_comment(me, target, text))
    gh("issue", "edit", number, "-R", REPO, "--add-label", f"needs:{target}")
    print(f"Asked {target} on #{number}.")
    return 0


def cmd_answer(number: str, text: str) -> int:
    me = machine()
    gh("issue", "comment", number, "-R", REPO, "--body", f"From **{me}**: {text}")
    gh("issue", "edit", number, "-R", REPO, "--remove-label", f"needs:{me}")
    print(f"Answered #{number}; needs:{me} removed.")
    return 0


def current_branch(workdir: str | None = None) -> str:
    command = ["git", *(["-C", workdir] if workdir else []), "rev-parse", "--abbrev-ref", "HEAD"]
    return subprocess.run(command, capture_output=True, text=True).stdout.strip()


def branch_tickets(branch: str) -> list[str]:
    """Tickets a branch belongs to: its open PR, and the issue number in its name."""
    numbers: list[str] = []
    pr = gh(
        "pr",
        "list",
        "-R",
        REPO,
        "--head",
        branch,
        "--state",
        "open",
        "--json",
        "number",
        "-q",
        ".[0].number",
        check=False,
    ).strip()
    if pr:
        numbers.append(pr)
    issue = ticket_in_branch(branch)
    if issue and issue not in numbers:
        numbers.append(issue)
    return numbers


def conflicts(branch: str) -> list[str]:
    me = machine()
    problems = []
    for number in branch_tickets(branch):
        try:
            data = ticket(number)
        except SystemExit:
            continue
        others = [m for m in data["machines"] if m != me]
        if others:
            problems.append(f"#{number} ({data['title']}) is claimed by {', '.join(others)}")
    return problems


def cmd_check(branch: str | None) -> int:
    branch = branch or current_branch()
    problems = conflicts(branch)
    if problems:
        print(f"CONFLICT on {branch}: " + "; ".join(problems))
        return 1
    print(f"OK: {branch} has no claim by another machine")
    return 0


def status_lines() -> list[str]:
    me = machine()
    items = [i for i in board_items() if i.get("content", {}).get("repository") == REPO]
    lines = [
        f"Scan64 coordination, this machine: {me}. Rules: {RULES}. Board: "
        f"https://github.com/users/{BOARD_OWNER}/projects/{BOARD_NUMBER}"
    ]
    for name in machines():
        held = [
            i
            for i in items
            if f"machine:{name}" in i.get("labels", [])
            and i.get("status") in ("In progress", "In review")
        ]
        lines.append(
            f"{name}{' (this machine)' if name == me else ''}: "
            + (
                ", ".join(
                    f"#{i['content']['number']} [{i['status']}] {i['content']['title'][:60]}"
                    for i in held
                )
                or "nothing claimed"
            )
        )
    waiting = search_open(f"needs:{me}")
    lines.append(
        "Waiting on this machine (read the latest 'For **"
        + me
        + "**' comment, act, then `scan64-coord answer <N> <text>`): "
        + (", ".join(f"#{i['number']} {i['title'][:60]}" for i in waiting) or "nothing")
    )
    need = search_open(f"needs-test:{os_of(me)}")
    if need:
        lines.append(
            f"Needs a test run on this OS ({os_of(me)}): "
            + ", ".join(f"#{i['number']} {i['title'][:60]}" for i in need)
        )
    free = [
        i
        for i in items
        if i.get("status") == "Todo"
        and not any(label.startswith("machine:") for label in i.get("labels", []))
        and not i.get("blocked by")
    ]
    lines.append(
        "Unclaimed and unblocked: "
        + (
            ", ".join(f"#{i['content']['number']} {i['content']['title'][:50]}" for i in free)
            or "none"
        )
    )
    lines.append(
        "Before you start a ticket: scan64-coord claim <N>. A push to a ticket "
        "another machine claimed is blocked."
    )
    return lines


# --- Worktree cleanup ---------------------------------------------------------------

# A worktree whose index or HEAD changed this recently may still be in use.
GC_RECENT_SECONDS = 3600
# Ignored paths gc may delete with a worktree: caches and build output that a
# rebuild recreates. Any other ignored file (.env, a database, notes) keeps it.
REBUILDABLE = frozenset(
    {
        ".venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "test-results",
        "playwright-report",
        "dist",
        "build",
        "target",
    }
)
# Seconds allowed for each gh call gc makes, so the session-start hook can't hang.
GC_GH_TIMEOUT = 30


def git(workdir: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "--no-optional-locks", "-C", workdir, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def gc_verdict(
    *,
    locked: bool,
    in_use: bool | None,
    changes: list[str],
    recent: bool,
    merged: bool,
    pr_closed: bool,
    pushed: bool,
    disposable: bool,
    superseded: bool,
    reviewed: bool,
) -> tuple[bool, str]:
    """Whether `gc` removes a worktree, and why.

    Removal needs two things: nothing can be lost (no changes or non-rebuildable files,
    no process using it, its commit on origin or in a pull request) and the work is over
    (merged or closed; for review checkouts also moved past or reviewed). `in_use` is
    None when processes can't be checked, and then the worktree is kept. `disposable`
    marks review checkouts: a detached HEAD or a `review/*` branch.
    """
    if locked:
        return False, "locked"
    if in_use is None:
        return False, "can't check for processes using it (no lsof)"
    if in_use:
        return False, "a process is using it"
    if changes:
        return False, "has files that would be lost: " + ", ".join(changes[:3])
    if recent:
        return False, "used in the last hour"
    if merged:
        return True, "merged into main or an epic"
    if pr_closed:
        return True, "its pull request is merged or closed"
    if not pushed:
        return False, "has commits that are not on origin"
    if disposable and superseded:
        return True, "review checkout; its branch on origin has moved past it"
    if disposable and reviewed:
        return True, "review checkout; scan64/review is set on its commit"
    return False, "work in progress"


def linked_worktrees(repo: str) -> list[dict[str, str]]:
    """The repository's linked worktrees (never the main checkout), from `git worktree list`."""
    entries: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in git(repo, "worktree", "list", "--porcelain").stdout.splitlines() + [""]:
        if not line:
            if current:
                entries.append(current)
            current = {}
            continue
        key, _, value = line.partition(" ")
        current[key] = value or "true"
    return entries[1:]


def process_cwds() -> list[str] | None:
    """Working directories of running processes; None where `lsof` is unavailable."""
    if not shutil.which("lsof"):
        return None
    out = subprocess.run(
        ["lsof", "-a", "-d", "cwd", "-Fn"], capture_output=True, text=True, encoding="utf-8"
    ).stdout
    return [os.path.realpath(line[1:]) for line in out.splitlines() if line.startswith("n")]


def unsafe_files(path: str) -> list[str]:
    """Changes and ignored files that removing the worktree would lose.

    Ignored caches and build output (REBUILDABLE) don't count; everything else does.
    """
    out = git(path, "status", "--porcelain", "--ignored").stdout
    unsafe = []
    for line in out.splitlines():
        status, name = line[:2], line[3:].rstrip("/")
        if status == "!!" and Path(name).name in REBUILDABLE:
            continue
        unsafe.append(name)
    return unsafe


def changed_since(path: str, seconds: int) -> bool:
    admin = git(path, "rev-parse", "--absolute-git-dir").stdout.strip()
    stamps = [
        os.path.getmtime(p) for p in (Path(admin, "index"), Path(admin, "HEAD")) if p.exists()
    ]
    return bool(stamps) and max(stamps) > time.time() - seconds


def pull_request_states() -> dict[str, str]:
    """Head commit -> state (OPEN, MERGED, CLOSED) of recent pull requests.

    Empty when `gh` fails, which only makes gc keep more.
    """
    args = ["pr", "list", "-R", REPO, "--state", "all", "--limit", "300"]
    out = gh(*args, "--json", "headRefOid,state", check=False, timeout=GC_GH_TIMEOUT)
    return {pr["headRefOid"]: pr["state"] for pr in json.loads(out or "[]")}


def has_review_status(commit: str) -> bool:
    """Whether a `scan64/review` status is set on the commit; False when `gh` fails."""
    query = '[.[] | select(.context == "scan64/review")] | length'
    out = gh(
        "api",
        f"repos/{REPO}/commits/{commit}/statuses",
        "--jq",
        query,
        check=False,
        timeout=GC_GH_TIMEOUT,
    )
    return out.strip() not in ("", "0")


def gc(repo: str, apply: bool, recent_seconds: int = GC_RECENT_SECONDS) -> list[str]:
    """Remove the repository's finished worktrees (dry run unless `apply`).

    Returns one line per linked worktree. Never touches the main checkout, never deletes
    a branch other than a local `review/*` branch, and stops if `git fetch` fails.
    """
    fetched = subprocess.run(
        ["git", "-C", repo, "fetch", "-q", "--prune", "origin"], capture_output=True, timeout=120
    )
    if fetched.returncode != 0:
        return ["gc: git fetch failed, nothing removed"]
    cwds = process_cwds()
    states: dict[str, str] | None = None
    lines: list[str] = []
    for entry in linked_worktrees(repo):
        path = os.path.realpath(entry["worktree"])
        if "prunable" in entry or not os.path.isdir(path):
            continue
        head = entry.get("HEAD", "")
        remote = [
            ref.strip()
            for ref in git(repo, "branch", "-r", "--contains", head).stdout.splitlines()
            if "->" not in ref
        ]
        branch = entry.get("branch", "").removeprefix("refs/heads/")
        own = [f"origin/{branch}"] if branch else remote
        if states is None:
            states = pull_request_states()
        pushed = bool(remote) or head in states
        remove, reason = gc_verdict(
            locked="locked" in entry,
            in_use=None
            if cwds is None
            else any(cwd == path or cwd.startswith(path + os.sep) for cwd in cwds),
            changes=unsafe_files(path),
            recent=changed_since(path, recent_seconds),
            merged=any(ref == "origin/main" or ref.startswith("origin/epic/") for ref in remote),
            pr_closed=states.get(head) in ("MERGED", "CLOSED"),
            pushed=pushed,
            disposable=not branch or branch.startswith("review/"),
            superseded=any(
                ref in remote and git(repo, "rev-parse", ref).stdout.strip() != head for ref in own
            ),
            reviewed=pushed and has_review_status(head),
        )
        if not remove:
            lines.append(f"kept {path}: {reason}")
            continue
        if not apply:
            lines.append(f"would remove {path}: {reason}")
            continue
        removed = git(repo, "worktree", "remove", "--force", path)
        if removed.returncode != 0:
            lines.append(f"failed to remove {path}: {removed.stderr.strip()}")
            continue
        if branch.startswith("review/"):
            git(repo, "branch", "-D", branch)
        lines.append(f"removed {path}: {reason}")
    git(repo, "worktree", "prune")
    return lines


def gc_repo() -> str:
    """The Scan64 checkout to clean: the current one, or the one setup ran in."""
    top = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    if top:
        origin = subprocess.run(
            ["git", "remote", "get-url", "origin"], capture_output=True, text=True
        ).stdout
        if REPO.lower() in origin.lower():
            return str(Path(top).parent)
    repo_file = INSTALL_DIR / "repo"
    if repo_file.exists():
        return repo_file.read_text(encoding="utf-8").strip()
    raise SystemExit("Run inside a Scan64 checkout, or run `scan64-coord setup <machine>` first.")


def cmd_gc(apply: bool) -> int:
    lines = gc(gc_repo(), apply)
    print("\n".join(lines) if lines else "No linked worktrees.")
    if not apply and any(line.startswith("would remove") for line in lines):
        print("Dry run. Run `scan64-coord gc --apply` to remove them.")
    return 0


def refresh_installed_copy() -> None:
    """Update the installed copy from the first of TOOL_REFS that has the tool.

    Fetches only those branches; the checkout's working tree is never touched.
    Any failure keeps the installed copy as it is.
    """
    repo_file = INSTALL_DIR / "repo"
    if not repo_file.exists():
        return
    repo = repo_file.read_text(encoding="utf-8").strip()
    installed = INSTALL_DIR / "scan64_coord.py"
    for ref in TOOL_REFS:
        remote, _, branch = ref.partition("/")
        try:
            subprocess.run(
                ["git", "-C", repo, "fetch", "-q", remote, branch], capture_output=True, timeout=15
            )
            shown = subprocess.run(
                ["git", "-C", repo, "show", f"{ref}:{TOOL_PATH}"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired):
            return
        if shown.returncode == 0 and shown.stdout:
            if shown.stdout != installed.read_text(encoding="utf-8"):
                installed.write_text(shown.stdout, encoding="utf-8")
            return


def cmd_hook_session() -> int:
    refresh_installed_copy()
    try:
        text = "\n".join(status_lines())
    except SystemExit as error:
        text = f"scan64-coord: status unavailable ({error})"
    try:
        removed = [line for line in gc(gc_repo(), apply=True) if line.startswith("removed ")]
        if removed:
            text += "\nFinished worktrees removed by `scan64-coord gc`:\n" + "\n".join(removed)
    except (SystemExit, OSError, subprocess.TimeoutExpired) as error:
        text += f"\nscan64-coord gc did not run ({error})"
    print(
        json.dumps(
            {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}
        )
    )
    return 0


def cmd_hook_pretool() -> int:
    event = json.load(sys.stdin)
    command = event.get("tool_input", {}).get("command", "")
    if not is_push_command(command):
        return 0
    match = re.search(r"git\s+-C\s+(\S+)", command)
    workdir = match.group(1) if match else (event.get("cwd") or os.getcwd())
    head = re.search(r"--head\s+(\S+)", command)
    branch = head.group(1) if head else current_branch(workdir)
    origin = subprocess.run(
        ["git", "-C", workdir, "remote", "get-url", "origin"], capture_output=True, text=True
    ).stdout
    if not branch or REPO.lower() not in origin.lower():
        return 0
    try:
        problems = conflicts(branch)
    except SystemExit:
        return 0
    if problems:
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": "scan64-coord: "
                        + "; ".join(problems)
                        + ". Another machine owns this ticket. Pick another ticket, or agree a "
                        "handover and run `scan64-coord claim <N> --takeover`.",
                    }
                }
            )
        )
    return 0


def cmd_install_hooks() -> int:
    installed = INSTALL_DIR / "scan64_coord.py"
    if not installed.exists():
        print("Run `scan64-coord setup <machine>` first.")
        return 2
    settings: dict[str, Any] = (
        json.loads(CLAUDE_SETTINGS.read_text(encoding="utf-8")) if CLAUDE_SETTINGS.exists() else {}
    )
    messages = merge_hooks(settings, f'"{sys.executable}" "{installed}"')
    print("\n".join(messages))
    if CLAUDE_SETTINGS.exists():
        CLAUDE_SETTINGS.with_name("settings.json.bak-scan64coord").write_text(
            CLAUDE_SETTINGS.read_text(encoding="utf-8"), encoding="utf-8"
        )
    CLAUDE_SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    CLAUDE_SETTINGS.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {CLAUDE_SETTINGS}. Open /hooks once (or restart Claude Code) to load them.")
    return 0


def cmd_setup(name: str) -> int:
    """One-time machine setup: machine name, installed copy and a `scan64-coord` command."""
    known = machines()
    if name not in known:
        print(f"Machine must be one of the machine:* labels in {REPO}: {', '.join(known)}")
        return 2
    MACHINE_FILE.parent.mkdir(parents=True, exist_ok=True)
    MACHINE_FILE.write_text(name + "\n", encoding="utf-8")
    print(f"Machine name: {name} ({MACHINE_FILE})")
    source = Path(__file__).resolve()
    repo = subprocess.run(
        ["git", "-C", str(source.parent), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    installed = INSTALL_DIR / "scan64_coord.py"
    installed.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    (INSTALL_DIR / "repo").write_text(repo + "\n", encoding="utf-8")
    print(f"Installed copy: {installed} (refreshed from {' or '.join(TOOL_REFS)} of {repo})")
    bin_dir = Path.home() / ".local" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        wrapper = bin_dir / "scan64-coord.cmd"
        wrapper.write_text(f'@"{sys.executable}" "{installed}" %*\r\n', encoding="utf-8")
    else:
        wrapper = bin_dir / "scan64-coord"
        wrapper.write_text(
            f'#!/usr/bin/env bash\nexec "{sys.executable}" "{installed}" "$@"\n', encoding="utf-8"
        )
        wrapper.chmod(0o755)
    print(f"Command: {wrapper} (put {bin_dir} on PATH if it is not)")
    print("Claude Code users: run `scan64-coord install-hooks` next.")
    print()
    os.environ["SCAN64_MACHINE"] = name
    print("\n".join(status_lines()))
    return 0


def release_status(flags: list[str]) -> str:
    if "--done" in flags:
        return "Done"
    return "In review" if "--review" in flags else "Todo"


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 0
    command, rest = argv[0], argv[1:]
    if command == "setup" and rest:
        return cmd_setup(rest[0])
    if command == "install-hooks":
        return cmd_install_hooks()
    if command == "status":
        print("\n".join(status_lines()))
        return 0
    if command == "claim" and rest:
        return cmd_claim(rest[0], "--takeover" in rest)
    if command == "split" and len(rest) >= 2:
        return cmd_split(rest[0], " ".join(r for r in rest[1:] if not r.startswith("--")))
    if command == "release" and rest:
        return cmd_release(rest[0], release_status(rest[1:]))
    if command == "ask" and len(rest) >= 3:
        return cmd_ask(rest[0], rest[1], " ".join(rest[2:]))
    if command == "answer" and len(rest) >= 2:
        return cmd_answer(rest[0], " ".join(rest[1:]))
    if command == "check":
        return cmd_check(rest[0] if rest else None)
    if command == "gc":
        return cmd_gc("--apply" in rest)
    if command == "hook-session":
        return cmd_hook_session()
    if command == "hook-pretool":
        return cmd_hook_pretool()
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
