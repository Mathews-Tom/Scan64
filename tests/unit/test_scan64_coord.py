import importlib.util
import subprocess
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "scan64_coord.py"
spec = importlib.util.spec_from_file_location("scan64_coord", SCRIPT)
assert spec is not None and spec.loader is not None
coord = importlib.util.module_from_spec(spec)
spec.loader.exec_module(coord)


@pytest.mark.parametrize(
    ("machine", "os_name"),
    [("linux-5090", "linux"), ("windows-4080", "windows"), ("mac", "mac")],
)
def test_os_of_matches_the_needs_test_labels(machine: str, os_name: str) -> None:
    assert coord.os_of(machine) == os_name


@pytest.mark.parametrize(
    ("branch", "ticket"),
    [
        ("feat/397-engine-compat", "397"),
        ("fix/404", "404"),
        ("epic/371", "371"),
        ("main", None),
        ("feat/engine-compat-397", None),
        ("fix/7-too-short", None),
    ],
)
def test_ticket_in_branch(branch: str, ticket: str | None) -> None:
    assert coord.ticket_in_branch(branch) == ticket


@pytest.mark.parametrize(
    ("command", "blocked"),
    [
        ("git push origin feat/397-engine-compat", True),
        ("git -C ../Scan64 push", True),
        ("gh pr create --base epic/371 --fill", True),
        ("git pull --ff-only", False),
        ("gh pr view 397", False),
    ],
)
def test_is_push_command(command: str, blocked: bool) -> None:
    assert coord.is_push_command(command) is blocked


def test_merge_hooks_keeps_existing_hooks_and_is_idempotent() -> None:
    existing = {"type": "command", "command": "other-tool pre", "timeout": 5}
    settings: dict[str, Any] = {
        "model": "kept",
        "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [existing]}]},
    }

    first = coord.merge_hooks(settings, '"python" "/x/scan64_coord.py"')
    second = coord.merge_hooks(settings, '"python" "/x/scan64_coord.py"')

    assert first == ["SessionStart: installed", "PreToolUse: installed"]
    assert second == ["SessionStart: already installed", "PreToolUse: already installed"]
    assert settings["model"] == "kept"
    bash = settings["hooks"]["PreToolUse"]
    assert len(bash) == 1
    assert [hook["command"] for hook in bash[0]["hooks"]] == [
        "other-tool pre",
        '"python" "/x/scan64_coord.py" hook-pretool',
    ]
    assert settings["hooks"]["SessionStart"] == [
        {
            "matcher": "",
            "hooks": [
                {
                    "type": "command",
                    "command": '"python" "/x/scan64_coord.py" hook-session',
                    "timeout": 30,
                }
            ],
        }
    ]


def test_merge_hooks_repoints_hooks_from_the_earlier_standalone_repo() -> None:
    old = '"python" "/home/u/.local/share/scan64-coord/scan64_coord.py"'
    settings: dict[str, Any] = {
        "hooks": {
            "SessionStart": [
                {"matcher": "", "hooks": [{"type": "command", "command": f"{old} hook-session"}]}
            ],
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [{"type": "command", "command": f"{old} hook-pretool"}],
                }
            ],
        }
    }

    messages = coord.merge_hooks(settings, '"python" "/new/scan64_coord.py"')

    assert messages == ["SessionStart: updated", "PreToolUse: updated"]
    commands = [
        hook["command"]
        for event in ("SessionStart", "PreToolUse")
        for entry in settings["hooks"][event]
        for hook in entry["hooks"]
    ]
    assert commands == [
        '"python" "/new/scan64_coord.py" hook-session',
        '"python" "/new/scan64_coord.py" hook-pretool',
    ]


@pytest.mark.parametrize(
    ("flags", "status"),
    [([], "Todo"), (["--review"], "In review"), (["--done"], "Done")],
)
def test_release_status(flags: list[str], status: str) -> None:
    assert coord.release_status(flags) == status


def test_machine_rejects_an_unset_name(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SCAN64_MACHINE", "")
    monkeypatch.setattr(coord, "MACHINE_FILE", tmp_path / "machine")
    with pytest.raises(SystemExit, match="Unknown machine"):
        coord.machine()


_FINISHED = {
    "locked": False,
    "in_use": False,
    "dirty": False,
    "recent": False,
    "merged": False,
    "pr_closed": False,
    "pushed": True,
    "superseded": False,
    "reviewed": False,
}


@pytest.mark.parametrize(
    ("facts", "removed"),
    [
        ({"merged": True}, True),
        ({"pr_closed": True, "pushed": False}, True),
        ({"superseded": True}, True),
        ({"reviewed": True}, True),
        # Work in progress: pushed, open and not yet reviewed.
        ({}, False),
        # Nothing that could be lost is ever removed, however finished the work is.
        ({"merged": True, "dirty": True}, False),
        ({"reviewed": True, "pushed": False}, False),
        ({"superseded": True, "pushed": False}, False),
        ({"merged": True, "in_use": True}, False),
        ({"merged": True, "locked": True}, False),
        ({"merged": True, "recent": True}, False),
    ],
)
def test_gc_removes_only_finished_worktrees_that_lose_nothing(
    facts: dict[str, bool], removed: bool
) -> None:
    assert coord.gc_verdict(**{**_FINISHED, **facts})[0] is removed


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def test_gc_removes_a_merged_worktree_and_keeps_unpushed_and_dirty_ones(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(coord, "gh", lambda *args, check=True: "")
    origin = tmp_path / "origin.git"
    repo = tmp_path / "Scan64"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    subprocess.run(["git", "clone", "-q", str(origin), str(repo)], check=True, capture_output=True)
    for key, value in (("user.name", "t"), ("user.email", "t@example.com")):
        _git(repo, "config", key, value)
    _git(repo, "commit", "-q", "--allow-empty", "-m", "base")
    _git(repo, "push", "-q", "origin", "main")

    merged = tmp_path / "Scan64-pr1"
    _git(repo, "worktree", "add", "-q", "-b", "review/pr-1", str(merged), "main")
    unpushed = tmp_path / "Scan64-pr2"
    _git(repo, "worktree", "add", "-q", "--detach", str(unpushed), "main")
    _git(unpushed, "commit", "-q", "--allow-empty", "-m", "local only")
    dirty = tmp_path / "Scan64-pr3"
    _git(repo, "worktree", "add", "-q", "--detach", str(dirty), "main")
    (dirty / "notes.txt").write_text("draft")

    lines = coord.gc(str(repo), apply=True, recent_seconds=0)

    assert not merged.exists()
    assert _git(repo, "branch", "--list", "review/pr-1") == ""
    assert unpushed.exists() and dirty.exists()
    assert sorted(line.split(":")[0].split()[0] for line in lines) == ["kept", "kept", "removed"]
