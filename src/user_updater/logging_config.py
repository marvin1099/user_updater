"""Consistent journal and rotating-file logging for update workers."""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOGGER_NAME = "user-updater"
_MAX_LOG_BYTES = 10 * 1024 * 1024
_BACKUP_COUNT = 5


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


def configure_logging(log_file: Path, private: bool = False) -> logging.Logger:
    """Configure journal output plus a bounded local audit/update log."""
    log_file = Path(log_file)
    mode = 0o600 if private else 0o644
    directory_mode = 0o700 if private else 0o755
    log_file.parent.mkdir(mode=directory_mode, parents=True, exist_ok=True)
    if log_file.parent.is_symlink() or not log_file.parent.is_dir():
        raise OSError(f"log directory must be a real directory: {log_file.parent}")
    if log_file.is_symlink():
        raise OSError(f"refusing to write through a symbolic-link log file: {log_file}")
    log_file.parent.chmod(directory_mode)
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
