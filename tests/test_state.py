from pathlib import Path

from user_updater.state import StateStore, UpdateState


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
