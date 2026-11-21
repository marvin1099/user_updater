"""Bounded-memory subprocess streaming for update commands."""

from __future__ import annotations

import fcntl
import os
import pty
import re
import selectors
import signal
import struct
import subprocess
import termios
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

_ANSI_ESCAPE = re.compile(
    r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\)|[@-_])"
)
_ANSI_SGR = re.compile(r"\x1b\[[0-?]*[ -/]*m")
_MAX_UNTERMINATED_LINE = 64 * 1024


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    duration_seconds: float
    cancelled: bool
    output_tail: tuple[str, ...]


def strip_ansi(text: str) -> str:
    """Return plain text for logs and command-result parsing."""
    return _ANSI_ESCAPE.sub("", text)


def _retain_sgr(text: str) -> str:
    """Keep SGR styling codes while dropping cursor and terminal-control codes."""
    pieces: list[str] = []
    offset = 0
    for match in _ANSI_ESCAPE.finditer(text):
        pieces.append(text[offset : match.start()])
        sequence = match.group()
        if _ANSI_SGR.fullmatch(sequence):
            pieces.append(sequence)
        offset = match.end()
    pieces.append(text[offset:])
    return "".join(pieces)


def run_command(
    command: Sequence[str],
    *,
    on_output: Callable[[str], None],
    on_progress: Callable[[str], None] | None = None,
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
    """Run an executable without a shell and stream its merged PTY output.

    The child sees a terminal on stdout/stderr so TTY-aware tools can emit color
    and progress. Stdin remains disconnected. A quiet child is reported through
    ``on_idle`` but is never timed out. Output memory is bounded to a recent tail
    plus one capped unterminated line.
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
    master_fd, slave_fd = pty.openpty()
    try:
        terminal_attributes = termios.tcgetattr(slave_fd)
        terminal_attributes[1] &= ~termios.ONLCR
        termios.tcsetattr(slave_fd, termios.TCSANOW, terminal_attributes)
        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 120, 0, 0))
        child_env = dict(env) if env is not None else os.environ.copy()
        child_env.setdefault("TERM", "xterm-256color")
        process = subprocess.Popen(
            list(command),
            stdin=subprocess.DEVNULL,
            stdout=slave_fd,
            stderr=subprocess.STDOUT,
            cwd=cwd,
            env=child_env,
            bufsize=0,
            close_fds=True,
            shell=False,
            start_new_session=True,
            **privilege_options,
        )
    except BaseException:
        os.close(master_fd)
        raise
    finally:
        os.close(slave_fd)

    selector = selectors.DefaultSelector()
    selector.register(master_fd, selectors.EVENT_READ)
    pending = ""
    progress_active = False
    cancelled = False
    cancelled_at: float | None = None
    eof = False

    def emit(line: str, *, progress: bool = False) -> None:
        nonlocal progress_active
        clean = strip_ansi(line)
        if clean.strip():
            clean = clean.strip()
            rendered = _retain_sgr(line)
            if progress:
                if progress_active and output_tail:
                    output_tail[-1] = clean
                else:
                    output_tail.append(clean)
                (on_progress or on_output)(rendered)
                progress_active = True
            else:
                output_tail.append(clean)
                on_output(rendered)
                progress_active = False

    def drain_text(text: str, final: bool = False) -> None:
        nonlocal pending, progress_active
        for character in text:
            if character == "\r":
                if pending:
                    emit(pending, progress=True)
                    pending = ""
            elif character == "\n":
                if pending:
                    emit(pending, progress=progress_active)
                    pending = ""
                progress_active = False
            else:
                pending += character
                while len(pending) > _MAX_UNTERMINATED_LINE:
                    emit(pending[:_MAX_UNTERMINATED_LINE], progress=progress_active)
                    pending = pending[_MAX_UNTERMINATED_LINE:]
        if final:
            if pending:
                emit(pending, progress=progress_active)
                pending = ""
            progress_active = False

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
                    chunk = os.read(master_fd, 8192)
                except OSError:
                    chunk = b""
                if chunk:
                    now = time.monotonic()
                    last_output_at = now
                    last_idle_at = now
                    drain_text(chunk.decode("utf-8", errors="replace"))
                else:
                    eof = True
                    selector.unregister(master_fd)

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
        os.close(master_fd)
        if process.poll() is None:
            _signal_process_group(process, signal.SIGKILL)
            process.wait()


def _signal_process_group(process: subprocess.Popen[bytes], sig: signal.Signals) -> None:
    try:
        os.killpg(process.pid, sig)
    except ProcessLookupError:
        pass
