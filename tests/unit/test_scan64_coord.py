import importlib.util
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
