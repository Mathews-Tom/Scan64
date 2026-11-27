#!/usr/bin/env python3
"""scan64-coord: claim Scan64 tickets per machine so parallel machines never collide.

A claim is the `machine:<name>` label on the GitHub issue or PR. The Scan64
board (Status, Machine) mirrors it. The rules are in CONTRIBUTING.md, section
"Working across machines". Every machine runs the same commands:

  scan64-coord status              what every machine holds, what needs this OS
  scan64-coord claim N [--takeover]
                                   claim issue or PR N for this machine
  scan64-coord split N TITLE       new sub-issue for this machine's share of N, claimed here
  scan64-coord release N [--review|--done]
                                   --review keeps the claim and labels the ticket's open PRs;
                                   --done marks it merged; no flag gives the ticket back
  scan64-coord check [BRANCH]      exit 1 if BRANCH's ticket belongs to another machine
  scan64-coord setup MACHINE       one-time setup: machine name, installed copy, command
  scan64-coord install-hooks       add the Claude Code hooks to ~/.claude/settings.json
  scan64-coord hook-session        SessionStart hook: refresh the tool, print the status
  scan64-coord hook-pretool        PreToolUse hook: deny git push / gh pr create on a conflict

Machine names are the repository's `machine:<name>` labels. The part of a name
before the first "-" is its OS, which matches the `needs-test:<os>` labels.
This machine's name comes from $SCAN64_MACHINE or ~/.config/scan64/machine.
Needs the `gh` CLI, logged in.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO = "Mathews-Tom/Scan64"
BOARD_OWNER = "Mathews-Tom"
BOARD_NUMBER = "1"
RULES = "CONTRIBUTING.md, section 'Working across machines'"
# hook-session refreshes the installed copy from this ref of the checkout setup ran in.
TOOL_REF = "origin/main"
TOOL_PATH = "scripts/scan64_coord.py"
INSTALL_DIR = Path.home() / ".local" / "share" / "scan64-coord"
MACHINE_FILE = Path.home() / ".config" / "scan64" / "machine"
CLAUDE_SETTINGS = Path.home() / ".claude" / "settings.json"
_MACHINE_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_PUSH = re.compile(r"\bgit\b.*\bpush\b|\bgh\s+pr\s+create\b")


def gh(*args: str, check: bool = True) -> str:
    result = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8")
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

    Existing hooks stay. A hook that already runs scan64_coord.py is not added twice.
    Returns one message per event.
    """
    hooks = settings.setdefault("hooks", {})
    wanted = {
        "SessionStart": ("", f"{base} hook-session"),
        "PreToolUse": ("Bash", f"{base} hook-pretool"),
    }
    messages = []
    for event, (matcher, command) in wanted.items():
        entries = hooks.setdefault(event, [])
        if any(
            "scan64_coord.py" in hook.get("command", "")
            for entry in entries
            for hook in entry.get("hooks", [])
        ):
            messages.append(f"{event}: already installed")
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
    need = [i for i in items if f"needs-test:{os_of(me)}" in i.get("labels", [])]
    if need:
        lines.append(
            f"Needs a test run on this OS ({os_of(me)}): "
            + ", ".join(f"#{i['content']['number']} {i['content']['title'][:60]}" for i in need)
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


def refresh_installed_copy() -> None:
    """Update the installed copy from TOOL_REF of the checkout setup ran in.

    Fetches only TOOL_REF's branch; the checkout's working tree is never touched.
    Any failure keeps the installed copy as it is.
    """
    repo_file = INSTALL_DIR / "repo"
    if not repo_file.exists():
        return
    repo = repo_file.read_text(encoding="utf-8").strip()
    remote, _, branch = TOOL_REF.partition("/")
    try:
        subprocess.run(
            ["git", "-C", repo, "fetch", "-q", remote, branch], capture_output=True, timeout=15
        )
        shown = subprocess.run(
            ["git", "-C", repo, "show", f"{TOOL_REF}:{TOOL_PATH}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return
    installed = INSTALL_DIR / "scan64_coord.py"
    if (
        shown.returncode == 0
        and shown.stdout
        and shown.stdout != installed.read_text(encoding="utf-8")
    ):
        installed.write_text(shown.stdout, encoding="utf-8")


def cmd_hook_session() -> int:
    refresh_installed_copy()
    try:
        text = "\n".join(status_lines())
    except SystemExit as error:
        text = f"scan64-coord: status unavailable ({error})"
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
    print(f"Installed copy: {installed} (refreshed from {TOOL_REF} of {repo})")
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
    if command == "check":
        return cmd_check(rest[0] if rest else None)
    if command == "hook-session":
        return cmd_hook_session()
    if command == "hook-pretool":
        return cmd_hook_pretool()
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
