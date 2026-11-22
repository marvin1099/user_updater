import json
from io import StringIO

import pytest

from user_updater import admin_session, cli


def test_persistent_admin_session_dispatches_multiple_requests(monkeypatch) -> None:
    monkeypatch.setattr(admin_session.os, "geteuid", lambda: 0)
    dispatched: list[tuple[object, dict[str, object]]] = []
    monkeypatch.setattr(
        admin_session,
        "_dispatch",
        lambda action, payload: dispatched.append((action, payload)),
    )
    source = StringIO(
        '{"id":1,"action":"schedule-prompt","payload":{"task_id":"system"}}\n'
        '{"id":2,"action":"run-system-now","payload":{}}\n'
    )
    destination = StringIO()

    assert admin_session.run_admin_session(source, destination) == 0

    responses = [json.loads(line) for line in destination.getvalue().splitlines()]
    assert responses == [
        {"event": "ready"},
        {"id": 1, "ok": True, "message": ""},
        {"id": 2, "ok": True, "message": ""},
    ]
    assert dispatched == [
        ("schedule-prompt", {"task_id": "system"}),
        ("run-system-now", {}),
    ]


def test_admin_session_reports_bad_requests_and_continues(monkeypatch) -> None:
    monkeypatch.setattr(admin_session.os, "geteuid", lambda: 0)
    source = StringIO(
        '{"id":1,"action":"not-allowed","payload":{}}\n'
        '{"id":2,"action":"close","payload":{}}\n'
    )
    destination = StringIO()

    assert admin_session.run_admin_session(source, destination) == 0

    responses = [json.loads(line) for line in destination.getvalue().splitlines()]
    assert responses[1] == {
        "id": 1,
        "ok": False,
        "message": "unknown administrator action: 'not-allowed'",
    }
    assert responses[2]["ok"] is False


def test_admin_session_routes_only_fixed_privileged_operations(monkeypatch) -> None:
    writes: list[dict[str, object]] = []
    scheduled: list[tuple[str, bool]] = []
    spawned: list[str] = []
    monkeypatch.setattr(cli, "_write_admin_policy_mapping", writes.append)
    monkeypatch.setattr(
        cli,
        "_schedule_prompt",
        lambda task_id, *, system_mode: (
            scheduled.append((task_id, system_mode)) or 0
        ),
    )
    monkeypatch.setattr(admin_session, "_spawn_helper_action", spawned.append)

    admin_session._dispatch("write-policy", {"mapping": {"version": 1}})
    admin_session._dispatch("schedule-prompt", {"task_id": "user-tools"})
    admin_session._dispatch("run-system-now", {})
    admin_session._dispatch("run-user-tools-now", {})

    assert writes == [{"version": 1}]
    assert scheduled == [("user-tools", True)]
    assert spawned == ["run-now", "run-user-tools-now"]
    with pytest.raises(ValueError, match="unknown administrator action"):
        admin_session._dispatch("run", {"command": "arbitrary"})
