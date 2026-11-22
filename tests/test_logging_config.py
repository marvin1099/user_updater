from datetime import date, timedelta
from pathlib import Path

from user_updater import logging_config


def test_configured_log_includes_entries_and_uses_private_permissions(
    tmp_path: Path,
) -> None:
    log_file = tmp_path / "private" / "updater.log"
    today = date(2026, 5, 7)
    daily_file = logging_config.daily_log_path(log_file, today)
    logger = logging_config.configure_logging(log_file, private=True, log_date=today)

    logger.info("user package update started")
    for handler in logger.handlers:
        handler.flush()

    assert "user package update started" in daily_file.read_text(encoding="utf-8")
    assert log_file.parent.stat().st_mode & 0o777 == 0o700
    assert daily_file.stat().st_mode & 0o777 == 0o600


def test_run_log_stays_pinned_to_its_start_day_across_midnight(tmp_path: Path) -> None:
    log_file = tmp_path / "logs" / "updater.log"
    first_day = date(2026, 5, 7)
    next_day = first_day + timedelta(days=1)
    first_day_log = logging_config.daily_log_path(log_file, first_day)
    next_day_log = logging_config.daily_log_path(log_file, next_day)
    try:
        logger = logging_config.configure_logging(log_file, log_date=first_day)
        logger.info("update started")
        # An open handler stays attached to its start-day file through midnight.
        logger.info("update finished after midnight")
        for handler in logger.handlers:
            handler.flush()

        assert "update started" in first_day_log.read_text(encoding="utf-8")
        assert "update finished after midnight" in first_day_log.read_text(encoding="utf-8")
        assert not next_day_log.exists()

        logger = logging_config.configure_logging(log_file, log_date=next_day)
        logger.info("new day update")
        for handler in logger.handlers:
            handler.flush()
        assert "new day update" in next_day_log.read_text(encoding="utf-8")
    finally:
        logging_config.configure_logging(tmp_path / "cleanup" / "cleanup.log", private=True)


def test_rotating_log_keeps_a_bounded_number_of_backups(tmp_path: Path) -> None:
    previous_max = logging_config._MAX_LOG_BYTES
    previous_backups = logging_config._BACKUP_COUNT
    logging_config._MAX_LOG_BYTES = 180
    logging_config._BACKUP_COUNT = 2
    log_directory = tmp_path / "logs"
    log_directory.mkdir(mode=0o750)
    log_file = log_directory / "updater.log"
    daily_file = logging_config.daily_log_path(log_file)
    previous_backup = daily_file.with_name(f"{daily_file.name}.1")
    previous_backup.write_text("previous run\n", encoding="utf-8")
    previous_backup.chmod(0o600)
    try:
        logger = logging_config.configure_logging(log_file)
        assert previous_backup.stat().st_mode & 0o777 == 0o644
        for index in range(20):
            logger.info("update output %s %s", index, "x" * 80)
        for handler in logger.handlers:
            handler.flush()

        backups = list(log_directory.glob(f"{daily_file.name}.*"))
        assert len(backups) <= 2
        assert daily_file.exists()
        assert log_directory.stat().st_mode & 0o777 == 0o755
        assert daily_file.stat().st_mode & 0o777 == 0o644
        assert all(backup.stat().st_mode & 0o777 == 0o644 for backup in backups)
    finally:
        logging_config._MAX_LOG_BYTES = previous_max
        logging_config._BACKUP_COUNT = previous_backups
        logging_config.configure_logging(tmp_path / "cleanup" / "cleanup.log", private=True)


def test_current_log_prefers_today_but_falls_back_to_legacy_file(tmp_path: Path) -> None:
    base = tmp_path / "updater.log"
    today = date(2026, 5, 7)
    daily = logging_config.daily_log_path(base, today)
    base.write_text("legacy log\n", encoding="utf-8")

    assert logging_config.current_log_path(base, today) == base

    daily.write_text("daily log\n", encoding="utf-8")
    assert logging_config.current_log_path(base, today) == daily


def test_log_setup_prunes_expired_daily_files_and_keeps_recent_days(tmp_path: Path) -> None:
    previous_retention = logging_config._LOG_RETENTION_DAYS
    logging_config._LOG_RETENTION_DAYS = 7
    base = tmp_path / "updater.log"
    today = date(2026, 5, 7)
    expired = logging_config.daily_log_path(base, today - timedelta(days=7))
    expired_backup = expired.with_name(f"{expired.name}.1")
    retained = logging_config.daily_log_path(base, today - timedelta(days=6))
    unrelated = tmp_path / "other-2026-04-01.log"
    for path in (expired, expired_backup, retained, unrelated):
        path.write_text("log\n", encoding="utf-8")
    try:
        logging_config.configure_logging(base, log_date=today)

        assert not expired.exists()
        assert not expired_backup.exists()
        assert retained.exists()
        assert unrelated.exists()
    finally:
        logging_config._LOG_RETENTION_DAYS = previous_retention
        logging_config.configure_logging(tmp_path / "cleanup" / "cleanup.log", private=True)
