"""Persistent, polkit-authorized session for administrator GUI actions."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import TextIO

_MAX_REQUEST_BYTES = 65_536


def run_admin_session(
    input_stream: TextIO | None = None,
    output_stream: TextIO | None = None,
) -> int:
    """Serve a small JSON-lines API until the unprivileged GUI closes its pipe."""
    source = input_stream or sys.stdin
    destination = output_stream or sys.stdout
    if os.geteuid() != 0:
        print("administrator session must be launched through polkit", file=sys.stderr)
        return 1

    _write_message(destination, {"event": "ready"})
    for line in source:
        if len(line.encode("utf-8")) > _MAX_REQUEST_BYTES:
            _write_message(
                destination,
                {"id": None, "ok": False, "message": "administrator request is too large"},
            )
            return 1

        request_id: int | None = None
        stdout = io.StringIO()
        stderr = io.StringIO()
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("administrator request must be a JSON object")
            request_id = request.get("id")
            if type(request_id) is not int or request_id < 1:
                raise ValueError("administrator request has an invalid id")
            action = request.get("action")
            payload = request.get("payload", {})
            if not isinstance(payload, dict):
                raise ValueError("administrator request payload must be a JSON object")
            with redirect_stdout(stdout), redirect_stderr(stderr):
                _dispatch(action, payload)
            message = _captured_message(stdout, stderr)
            response = {"id": request_id, "ok": True, "message": message}
        except (OSError, RuntimeError, ValueError, TypeError) as exc:
            message = _captured_message(stdout, stderr) or str(exc)
            response = {
                "id": request_id,
                "ok": False,
                "message": message,
            }
        _write_message(destination, response)
    return 0


def _dispatch(action: object, payload: dict[str, object]) -> None:
    from . import cli

    if action == "write-policy":
        mapping = payload.get("mapping")
        if not isinstance(mapping, dict):
            raise ValueError("policy mapping must be a JSON object")
        cli._write_admin_policy_mapping(mapping)
        return
    if action == "schedule-prompt":
        task_id = payload.get("task_id")
        if task_id not in {"system", "user-tools"}:
            raise ValueError("unknown update task")
        if cli._schedule_prompt(str(task_id), system_mode=True) != 0:
            raise RuntimeError("could not schedule every requested update prompt")
        return
    if action == "run-system-now":
        _spawn_helper_action("run-now")
        return
    if action == "run-user-tools-now":
        _spawn_helper_action("run-user-tools-now")
        return
    raise ValueError(f"unknown administrator action: {action!r}")


def _spawn_helper_action(action: str) -> None:
    helper = Path(sys.argv[0]).resolve(strict=True)
    subprocess.Popen(
        [str(helper), action],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        start_new_session=True,
    )


def _captured_message(stdout: io.StringIO, stderr: io.StringIO) -> str:
    return "\n".join(
        text.strip() for text in (stdout.getvalue(), stderr.getvalue()) if text.strip()
    )


def _write_message(destination: TextIO, message: dict[str, object]) -> None:
    destination.write(json.dumps(message, separators=(",", ":")) + "\n")
    destination.flush()
