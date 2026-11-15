"""Bounded-memory subprocess streaming for update commands."""

from __future__ import annotations

import os
import re
import selectors
import signal
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_LINE_BREAK = re.compile(r"[\r\n]+")
_MAX_UNTERMINATED_LINE = 64 * 1024


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    duration_seconds: float
    cancelled: bool
    output_tail: tuple[str, ...]


def run_command(
    command: Sequence[str],
    *,
    on_output: Callable[[str], None],
    on_idle: Callable[[float], None] | None = None,
    env: Mapping[str, str] | None = None,
    cwd: Path | None = None,
    user_id: int | None = None,
    group_id: int | None = None,
    cancel_event: threading.Event | None = None,
    quiet_warning_seconds: float | None = 90,
    poll_interval: float = 0.25,
    terminate_grace_seconds: float = 5,
    tail_lines: int = 200,
) -> CommandResult:
    """Run an executable without a shell and stream its merged output.

    A quiet child is reported through ``on_idle`` but is never timed out. The
    process group is terminated only when an explicit cancellation is set.
    Output memory is bounded to a recent tail plus one capped unterminated line.
    """
    if isinstance(command, (str, bytes)) or not command or not command[0]:
        raise ValueError("command must be a non-empty sequence of arguments")
    if poll_interval <= 0:
        raise ValueError("poll_interval must be greater than zero")

    started = time.monotonic()
    last_output_at = started
    last_idle_at = started
    output_tail: deque[str] = deque(maxlen=max(1, tail_lines))
    privilege_options: dict[str, object] = {}
    if user_id is not None:
        privilege_options["user"] = user_id
        privilege_options["group"] = group_id
        privilege_options["extra_groups"] = ()
    process = subprocess.Popen(
        list(command),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=cwd,
        env=dict(env) if env is not None else None,
        bufsize=0,
        close_fds=True,
        shell=False,
        start_new_session=True,
        **privilege_options,
    )
    if process.stdout is None:
        process.kill()
        raise RuntimeError("failed to open child output pipe")

    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    pending = ""
    cancelled = False
    cancelled_at: float | None = None
    eof = False

    def emit(line: str) -> None:
        clean = _ANSI_ESCAPE.sub("", line).strip()
        if clean:
            output_tail.append(clean)
            on_output(clean)

    def drain_text(text: str, final: bool = False) -> None:
        nonlocal pending
        pending += text
        parts = _LINE_BREAK.split(pending)
        if final:
            complete, pending = parts, ""
        else:
            complete, pending = parts[:-1], parts[-1]
        for line in complete:
            emit(line)
        while len(pending) > _MAX_UNTERMINATED_LINE:
            emit(pending[:_MAX_UNTERMINATED_LINE])
            pending = pending[_MAX_UNTERMINATED_LINE:]

    try:
        while not eof:
            now = time.monotonic()
            if cancel_event is not None and cancel_event.is_set() and not cancelled:
                cancelled = True
                cancelled_at = now
                _signal_process_group(process, signal.SIGTERM)

            if (
                cancelled
                and cancelled_at is not None
                and process.poll() is None
                and now - cancelled_at >= terminate_grace_seconds
            ):
                _signal_process_group(process, signal.SIGKILL)
                cancelled_at = now + 10**9

            events = selector.select(poll_interval)
            if events:
                try:
                    chunk = os.read(process.stdout.fileno(), 8192)
                except OSError:
                    chunk = b""
                if chunk:
                    now = time.monotonic()
                    last_output_at = now
                    last_idle_at = now
                    drain_text(chunk.decode("utf-8", errors="replace"))
                else:
                    eof = True
                    selector.unregister(process.stdout)

            now = time.monotonic()
            if (
                not eof
                and on_idle is not None
                and quiet_warning_seconds is not None
                and quiet_warning_seconds > 0
                and now - last_output_at >= quiet_warning_seconds
                and now - last_idle_at >= quiet_warning_seconds
            ):
                on_idle(now - last_output_at)
                last_idle_at = now

        drain_text("", final=True)
        returncode = process.wait()
        return CommandResult(
            returncode=returncode,
            duration_seconds=time.monotonic() - started,
            cancelled=cancelled,
            output_tail=tuple(output_tail),
        )
    finally:
        selector.close()
        process.stdout.close()
        if process.poll() is None:
            _signal_process_group(process, signal.SIGKILL)
            process.wait()


def _signal_process_group(process: subprocess.Popen[bytes], sig: signal.Signals) -> None:
    try:
        os.killpg(process.pid, sig)
    except ProcessLookupError:
        pass
