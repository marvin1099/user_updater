"""Date-pinned journal and rotating-file logging for update workers."""

from __future__ import annotations

import logging
import os
import sys
from datetime import date, datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOGGER_NAME = "user-updater"
_MAX_LOG_BYTES = 10 * 1024 * 1024
_BACKUP_COUNT = 5
_LOG_RETENTION_DAYS = 30


class _ModeRotatingFileHandler(RotatingFileHandler):
    def __init__(self, filename: Path, file_mode: int) -> None:
        self.file_mode = file_mode
        super().__init__(
            filename,
            maxBytes=_MAX_LOG_BYTES,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
            delay=False,
        )

    def _open(self):
        stream = super()._open()
        os.chmod(self.baseFilename, self.file_mode)
        return stream


def configure_logging(
    log_file: Path,
    private: bool = False,
    *,
    log_date: date | None = None,
) -> logging.Logger:
    """Configure a date-pinned local log plus journal output for this run."""
    base_log_file = Path(log_file)
    selected_date = log_date or datetime.now().astimezone().date()
    log_file = daily_log_path(base_log_file, selected_date)
    mode = 0o600 if private else 0o644
    directory_mode = 0o700 if private else 0o755
    log_file.parent.mkdir(mode=directory_mode, parents=True, exist_ok=True)
    if log_file.parent.is_symlink() or not log_file.parent.is_dir():
        raise OSError(f"log directory must be a real directory: {log_file.parent}")
    if log_file.is_symlink():
        raise OSError(f"refusing to write through a symbolic-link log file: {log_file}")
    log_file.parent.chmod(directory_mode)
    _prune_expired_daily_logs(base_log_file, selected_date)
    for index in range(1, _BACKUP_COUNT + 1):
        backup = log_file.with_name(f"{log_file.name}.{index}")
        if backup.is_file() and not backup.is_symlink():
            backup.chmod(mode)

    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s pid=%(process)d %(threadName)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    file_handler = _ModeRotatingFileHandler(log_file, mode)
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    return logger


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
