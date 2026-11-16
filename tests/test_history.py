from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from user_updater.history import HistoryError, RunHistoryStore
from user_updater.state import UpdateState

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=ZoneInfo("UTC"))


def test_run_history_keeps_the_newest_summaries_in_newest_first_order(
    tmp_path: Path,
) -> None:
    store = RunHistoryStore(tmp_path / "history.json", limit=2)
    for index in range(3):
        finished = NOW + timedelta(minutes=index)
        store.append(
            "system",
            UpdateState(
                phase="succeeded" if index != 1 else "failed",
                run_id=f"run-{index}",
                scheduled_for=(finished - timedelta(minutes=5)).isoformat(),
                started_at=(finished - timedelta(minutes=2)).isoformat(),
                finished_at=finished.isoformat(),
                returncode=0 if index != 1 else 1,
                message=f"run {index} result",
                last_output=("not copied into summary",),
            ),
        )

    records = store.load()

    assert [(record.run_id, record.phase) for record in records] == [
        ("run-2", "succeeded"),
        ("run-1", "failed"),
    ]
    assert records[1].message == "run 1 result"
    assert not hasattr(records[0], "last_output")
    assert store.path.stat().st_mode & 0o777 == 0o600


def test_run_history_rejects_malformed_files_without_returning_partial_data(
    tmp_path: Path,
) -> None:
    path = tmp_path / "history.json"
    path.write_text('{"version":1,"runs":[{"phase":"running"}]}', encoding="utf-8")

    with pytest.raises(HistoryError):
        RunHistoryStore(path).load()


def test_history_limit_is_applied_independently_per_task(tmp_path: Path) -> None:
    store = RunHistoryStore(tmp_path / "history.json", limit=2)
    for task in ("system", "user-tools"):
        for index in range(3):
            store.append(
                task,
                UpdateState(
                    phase="succeeded",
                    run_id=f"{task}-{index}",
                    finished_at=(NOW + timedelta(minutes=index)).isoformat(),
                ),
            )

    records = store.load()

    assert sum(record.task == "system" for record in records) == 2
    assert sum(record.task == "user-tools" for record in records) == 2
