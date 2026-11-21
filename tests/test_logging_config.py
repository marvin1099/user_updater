from pathlib import Path

from user_updater import logging_config


def test_configured_log_includes_entries_and_uses_private_permissions(
    tmp_path: Path,
) -> None:
    log_file = tmp_path / "private" / "updater.log"
    logger = logging_config.configure_logging(log_file, private=True)

    logger.info("user package update started")
    for handler in logger.handlers:
        handler.flush()

    assert "user package update started" in log_file.read_text(encoding="utf-8")
    assert log_file.parent.stat().st_mode & 0o777 == 0o700
    assert log_file.stat().st_mode & 0o777 == 0o600


def test_rotating_log_keeps_a_bounded_number_of_backups(tmp_path: Path) -> None:
    previous_max = logging_config._MAX_LOG_BYTES
    previous_backups = logging_config._BACKUP_COUNT
    logging_config._MAX_LOG_BYTES = 180
    logging_config._BACKUP_COUNT = 2
    log_directory = tmp_path / "logs"
    log_directory.mkdir(mode=0o750)
    log_file = log_directory / "updater.log"
    previous_backup = log_directory / "updater.log.1"
    previous_backup.write_text("previous run\n", encoding="utf-8")
    previous_backup.chmod(0o600)
    try:
        logger = logging_config.configure_logging(log_file)
        assert previous_backup.stat().st_mode & 0o777 == 0o644
        for index in range(20):
            logger.info("update output %s %s", index, "x" * 80)
        for handler in logger.handlers:
            handler.flush()

        backups = list(log_directory.glob("updater.log.*"))
        assert len(backups) <= 2
        assert log_file.exists()
        assert log_directory.stat().st_mode & 0o777 == 0o755
        assert log_file.stat().st_mode & 0o777 == 0o644
        assert all(backup.stat().st_mode & 0o777 == 0o644 for backup in backups)
    finally:
        logging_config._MAX_LOG_BYTES = previous_max
        logging_config._BACKUP_COUNT = previous_backups
        logging_config.configure_logging(tmp_path / "cleanup" / "cleanup.log", private=True)
