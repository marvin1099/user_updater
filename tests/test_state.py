import json
from pathlib import Path

import pytest

from user_updater.state import StateError, StateStore, UpdateState


def test_state_store_round_trips_and_atomically_replaces_state(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    expected = UpdateState(
        phase="awaiting_response",
        run_id="run-123",
        scheduled_for="2026-11-15T03:00:00+00:00",
        response_deadline="2026-11-15T03:05:00+00:00",
        deferrals_used=1,
        last_output=("Checking package updates",),
    )

    store.save(expected)

    assert store.load() == expected
    assert sorted(path.name for path in tmp_path.iterdir()) == ["state.json"]


def test_missing_state_starts_with_idle_state(tmp_path: Path) -> None:
    assert StateStore(tmp_path / "state.json").load() == UpdateState()


def test_state_store_round_trips_retry_group_and_counters(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    expected = UpdateState(
        phase="failed",
        run_id="attempt-2",
        retry_group_id="scheduled-run",
        automatic_retries_used=1,
        manual_retries_used=2,
        retry_after="2026-11-15T03:15:00+00:00",
    )

    store.save(expected)

    assert store.load() == expected


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("phase", "half-running"),
        ("phase", 2),
        ("run_id", 42),
        ("message", None),
        ("deferrals_used", -1),
        ("deferrals_used", True),
        ("returncode", "0"),
        ("automatic_retries_used", -1),
        ("manual_retries_used", True),
    ],
)
def test_state_store_rejects_invalid_field_types_and_values(
    tmp_path: Path, field: str, value: object
) -> None:
    path = tmp_path / "state.json"
    path.write_text(json.dumps({field: value}), encoding="utf-8")

    with pytest.raises(StateError):
        StateStore(path).load()
