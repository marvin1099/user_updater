"""Date-pinned journal and rotating-file logging for update workers."""

from __future__ import annotations

import logging
import os
import stat
import sys
from datetime import date, datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOGGER_NAME = "user-updater"
_MAX_LOG_BYTES = 10 * 1024 * 1024
_BACKUP_COUNT = 5
_LOG_RETENTION_DAYS = 30


class _ModeRotatingFileHandler(RotatingFileHandler):
    def __init__(
        self,
        filename: Path,
        file_mode: int,
        owner_uid: int | None = None,
        owner_gid: int | None = None,
    ) -> None:
        self.file_mode = file_mode
        self.owner_uid = owner_uid
        self.owner_gid = owner_gid
        super().__init__(
            filename,
            maxBytes=_MAX_LOG_BYTES,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
            delay=False,
        )

    def _open(self):
        if self.owner_uid is not None or self.owner_gid is not None:
            flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_CLOEXEC
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(self.baseFilename, flags, 0o600)
            try:
                os.fchown(
                    descriptor,
                    -1 if self.owner_uid is None else self.owner_uid,
                    -1 if self.owner_gid is None else self.owner_gid,
                )
                os.fchmod(descriptor, self.file_mode)
                return os.fdopen(
                    descriptor,
                    self.mode,
                    encoding=self.encoding,
                    errors=self.errors,
                )
            except BaseException:
                os.close(descriptor)
                raise
        stream = super()._open()
        os.chmod(self.baseFilename, self.file_mode)
        return stream


def configure_logging(
    log_file: Path,
    private: bool = False,
    *,
    log_date: date | None = None,
    logger_name: str = _LOGGER_NAME,
    include_stderr: bool = True,
    file_mode: int | None = None,
    directory_mode: int | None = None,
    owner_uid: int | None = None,
    owner_gid: int | None = None,
) -> logging.Logger:
    """Configure a date-pinned local log plus journal output for this run."""
    base_log_file = Path(log_file)
    selected_date = log_date or datetime.now().astimezone().date()
    log_file = daily_log_path(base_log_file, selected_date)
    mode = file_mode if file_mode is not None else (0o600 if private else 0o644)
    selected_directory_mode = (
        directory_mode if directory_mode is not None else (0o700 if private else 0o755)
    )
    log_file.parent.mkdir(mode=selected_directory_mode, parents=True, exist_ok=True)
    if log_file.parent.is_symlink() or not log_file.parent.is_dir():
        raise OSError(f"log directory must be a real directory: {log_file.parent}")
    if log_file.is_symlink():
        raise OSError(f"refusing to write through a symbolic-link log file: {log_file}")
    log_file.parent.chmod(selected_directory_mode)
    _prune_expired_daily_logs(base_log_file, selected_date)
    _secure_log_file(log_file, mode, owner_uid, owner_gid)
    for index in range(1, _BACKUP_COUNT + 1):
        backup = log_file.with_name(f"{log_file.name}.{index}")
        if backup.is_file() and not backup.is_symlink():
            _secure_log_file(backup, mode, owner_uid, owner_gid)

    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s pid=%(process)d %(threadName)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    file_handler = _ModeRotatingFileHandler(log_file, mode, owner_uid, owner_gid)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    if include_stderr:
        stream_handler = logging.StreamHandler(sys.stderr)
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)
    return logger


def configure_user_output_logging(
    log_file: Path,
    owner_uid: int,
    owner_gid: int,
    *,
    log_date: date | None = None,
) -> logging.Logger:
    """Create a user-readable log for personal-tool output without journal output."""
    return configure_logging(
        log_file,
        log_date=log_date,
        logger_name=f"{_LOGGER_NAME}.user-tools-{owner_uid}",
        include_stderr=False,
        file_mode=0o600,
        directory_mode=0o755,
        owner_uid=owner_uid,
        owner_gid=owner_gid,
    )


def daily_log_path(log_file: Path, log_date: date | None = None) -> Path:
    """Return the local-calendar-day log path derived from the configured base name."""
    base = Path(log_file)
    selected_date = log_date or datetime.now().astimezone().date()
    return base.with_name(f"{base.stem}-{selected_date.isoformat()}{base.suffix}")


def current_log_path(log_file: Path, log_date: date | None = None) -> Path:
    """Find today's dated log, falling back to the pre-daily-logs filename."""
    base = Path(log_file)
    dated = daily_log_path(base, log_date)
    if dated.is_file() or not base.is_file():
        return dated
    return base


def _secure_log_file(
    path: Path,
    file_mode: int,
    owner_uid: int | None,
    owner_gid: int | None,
) -> None:
    try:
        flags = os.O_RDONLY | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        return
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise OSError(f"log path is not a regular file: {path}")
        if owner_uid is not None or owner_gid is not None:
            os.fchown(
                descriptor,
                -1 if owner_uid is None else owner_uid,
                -1 if owner_gid is None else owner_gid,
            )
        os.fchmod(descriptor, file_mode)
    finally:
        os.close(descriptor)


def _prune_expired_daily_logs(base_log_file: Path, today: date) -> None:
    cutoff = today - timedelta(days=_LOG_RETENTION_DAYS - 1)
    prefix = f"{base_log_file.stem}-"
    pattern = f"{prefix}????-??-??{base_log_file.suffix}*"
    for candidate in base_log_file.parent.glob(pattern):
        if (
            candidate.is_symlink()
            or not candidate.is_file()
            or not candidate.name.startswith(prefix)
        ):
            continue
        suffix = candidate.name[len(prefix) + 10 :]
        if suffix != base_log_file.suffix:
            backup_suffix = f"{base_log_file.suffix}."
            if not suffix.startswith(backup_suffix):
                continue
            backup_number = suffix[len(backup_suffix) :]
            if not backup_number.isdecimal() or not 1 <= int(backup_number) <= _BACKUP_COUNT:
                continue
        try:
            log_date = date.fromisoformat(candidate.name[len(prefix) : len(prefix) + 10])
        except ValueError:
            continue
        if log_date < cutoff:
            try:
                candidate.unlink()
            except OSError:
                continue
