# Windows-only test failures (epic/371 campaign)

Host: Windows 11, Git Bash, CPython 3.12.8 (uv), 32 cores, `core.autocrlf=false`.
These tests fail on this host on unchanged `origin/main` too (checked on 55effac), so the
epic did not cause them. They were deselected locally through `PYTEST_ADDOPTS`.
Raw pytest output: `windows-failures/*.log` next to this file.

## 1. tests/integration/test_web_serving.py::test_real_assets_and_missing_assets — every run

```
for path in ["/assets/missing.js", "/assets/missing", "/missing.css"]:
    response = web_client.get(path)
>   assert response.status_code == 404
E   assert 200 == 404
```

A missing asset returns 200 instead of 404 on Windows. Check the static or SPA fallback route:
how the path is joined or resolved, and backslash handling.

## 2. tests/unit/test_desktop_package_smoke.py::test_process_executable_identifies_the_running_interpreter — every run

```
E   AssertionError: assert False
E    +  where False = samefile('D:\\projects\\Scan64\\.venv\\Scripts\\python.exe')
E    +    where samefile = WindowsPath('C:/Users/<user>/AppData/Roaming/uv/python/cpython-3.12.8-windows-x86_64-none/python.exe').samefile
```

On Windows, `.venv\Scripts\python.exe` is a launcher, not a link to the base interpreter, so
`samefile` is False. The test compares `sys.executable` with the process executable.

## 3. tests/unit/test_maia_coverage_disclosure.py::test_config_requires_absolute_operator_paths — every run

Failing case: `binary_path = "lc0"` together with checkpoint `1500 = "/operator/maia-1500.pb.gz"`.

```
E   Expected regex: 'maia.binary_path must be absolute'
E   Actual message: 'Maia checkpoint 1500 weights path must be absolute'
```

`/operator/...` is not absolute on Windows (it has no drive letter), so the checkpoint check
fires before the binary-path check. Either the validation order is wrong, or the fixture needs
a path that is absolute on every platform.

## 4. tests/unit/test_puzzle_import_job.py::test_cancel_interrupts_stalled_http_stream — every run

```
>   assert finished(job).status == "cancelled"
E   Failed: Import did not finish promptly        (5 s deadline in finished())
```

Cancelling does not interrupt a stalled HTTP stream on Windows within 5 s. On Windows,
closing a socket does not wake a blocked read the way it does on Linux.

## 5. tests/property/test_mastery_monotonicity.py::test_repeated_diagnoses_monotonically_lower_mastery_and_uncertainty — intermittent

```
E   hypothesis.errors.DeadlineExceeded: Test took 203.54ms, which exceeds the deadline of 200.00ms.
```

Runtimes on `main` here: about 46 to 199 ms per example. Each example runs
`SQLModel.metadata.create_all` on a new in-memory database. Fix: `@settings(deadline=None)` or a
higher deadline.

Run alone, this file fails with `NoReferencedTableError: lessonattempt.opportunity_id ->
persistedlessonopportunity`, on `main` too. The test imports only `SkillState`, so other models
are registered only when another test module imports them first.

## 6. tests/integration/test_habit_archive.py::test_habit_archive_rejects_tampered_provenance_atomically — intermittent, crashes the interpreter

Three runs of this test (36 cases), on epic/371:

- Run 1: 1 failed, `[forged_game_source]`:
  ```
  sqlalchemy/orm/persistence.py:351
  >   for propkey in set(propkey_to_col).intersection(state_dict):
  E   TypeError: 'set_iterator' object does not support item assignment
  ```
- Run 2: 36 passed.
- Run 3: `Windows fatal exception: access violation` (segfault). Stack:
  ```
  sqlalchemy/sql/annotation.py:108 _gen_annotations_cache_key
  ... sqlalchemy/orm/session.py:3702 get
  src/scan64/api/data_lifecycle.py:470 _validate_habit_archive
  src/scan64/api/data_lifecycle.py:2985 import_player_data
  anyio/_backends/_asyncio.py:1033 run   (worker thread)
  ```
  The full-suite crash was in `delete_player_data` (`data_lifecycle.py:3384`, bulk delete),
  reached from `test_habit_archive.py:63 _delete`.

The same crash happens on `main` (55effac): access violation in this file, and in an earlier
baseline, failures in `test_active_barrier_finished_short_after_freeze_remains_restorable[True]`
and `test_habit_archive_checks_unresolved_games_inside_persisted_span_before_length_filter[missing_analysis-40-hex]`.

A `TypeError` inside SQLAlchemy internals followed by an access violation points to memory
corruption, not a test assertion. Lead, not verified: `tests/integration/conftest.py:30`
injects the test's `db_session` into the app with `get_session_override`, and FastAPI runs the
sync endpoints (`import_player_data`, `delete_player_data`) in an anyio worker thread. One
Session and its SQLite connection are then used from more than one thread. Check whether the
crash goes away with a session per request, or with `check_same_thread` / connection handling
on the test engine. Also check whether SQLAlchemy's compiled extensions are involved
(`DISABLE_SQLALCHEMY_CEXT_RUNTIME=1`).
