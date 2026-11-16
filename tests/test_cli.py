from __future__ import annotations

import threading
from pathlib import Path

from user_updater import cli
from user_updater.coordinator import TickAction
from user_updater.paths import AppPaths
from user_updater.policy import load_policy
from user_updater.sessions import DesktopUser


class _Logger:
    def info(self, *_args: object) -> None:
        pass


def test_system_and_per_user_jobs_are_independent_and_tick_in_parallel(
    monkeypatch, tmp_path: Path
) -> None:
    users = [
        DesktopUser("alice", 1001, 1001, tmp_path / "alice", tmp_path / "runtime-alice"),
        DesktopUser("bob", 1002, 1002, tmp_path / "bob", tmp_path / "runtime-bob"),
    ]
    paths = AppPaths(
        policy=tmp_path / "project.yaml",
        state=tmp_path / "system" / "state.json",
        user_state_dir=tmp_path / "system" / "users",
        builder_home=tmp_path / "system" / "builders",
        sudoers=tmp_path / "sudoers",
        request=tmp_path / "requests" / "user-tools.json",
        log_file=tmp_path / "logs" / "updater.log",
    )
    barrier = threading.Barrier(3)
    observed: list[tuple[Path, Path | None]] = []

    class ParallelService:
        def __init__(self, _policy, state_store, _workflow, **kwargs) -> None:
            self.state_store = state_store
            self.lock_file = kwargs["lock_file"]

        def tick(self) -> TickAction:
            barrier.wait(timeout=5)
            observed.append((self.state_store.path, self.lock_file))
            return TickAction.NOT_DUE

    monkeypatch.setattr(cli, "system_paths", lambda: paths)
    monkeypatch.setattr(cli, "load_policy", lambda _path=None: load_policy())
    monkeypatch.setattr(cli, "configure_logging", lambda *_args, **_kwargs: _Logger())
    monkeypatch.setattr(cli, "_require_root", lambda _action: None)
    monkeypatch.setattr(cli, "detect_graphical_users", lambda: users)
    monkeypatch.setattr(cli, "UpdateService", ParallelService)

    assert cli._tick(system_mode=True) == 0
    assert len(observed) == 3
    assert len({state_path for state_path, _lock in observed}) == 3
    assert len({lock_path for _state_path, lock_path in observed}) == 3
