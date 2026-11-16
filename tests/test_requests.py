from datetime import datetime
from pathlib import Path

import pytest

from user_updater.requests import (
    UserRequest,
    load_request,
    request_path,
    save_request,
    task_request_path,
)


def test_request_is_scoped_to_a_run_and_user_state_directory(tmp_path: Path) -> None:
    request_file = request_path(tmp_path)
    request = UserRequest(
        run_id="run-a",
        action="defer",
        hours=4,
        submitted_at=datetime.now().astimezone().isoformat(),
    )
    save_request(request_file, request)

    assert load_request(request_file, expected_uid=None, expected_run_id="run-a") == request
    assert load_request(request_file, expected_uid=None, expected_run_id="run-b") is None
    assert request_file.stat().st_mode & 0o777 == 0o600


def test_invalid_or_untrusted_request_is_ignored(tmp_path: Path) -> None:
    request_file = request_path(tmp_path)
    request_file.parent.mkdir(parents=True)
    request_file.write_text(
        '{"run_id":"r","action":"shell","hours":1,"submitted_at":"2026-11-15T03:00:00+00:00"}',
        encoding="utf-8",
    )

    assert load_request(request_file, expected_uid=None, expected_run_id="r") is None


def test_independent_tasks_get_separate_response_files(tmp_path: Path) -> None:
    system_request = task_request_path(tmp_path, "system")
    user_request = task_request_path(tmp_path, "user-tools")

    assert system_request != user_request
    assert system_request.name == "system.json"
    assert user_request.name == "user-tools.json"


def test_task_id_cannot_escape_the_user_request_directory(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="task_id"):
        task_request_path(tmp_path, "../system")
