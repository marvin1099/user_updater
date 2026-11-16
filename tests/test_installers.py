from pathlib import Path

from user_updater.installer import InstallPaths, SystemInstaller, UserInstaller, UserInstallPaths
from user_updater.policy import load_policy


class FakeSystemd:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def run(self, arguments: list[str], *, check: bool = True) -> None:
        self.calls.append([*arguments, f"check={check}"] if not check else list(arguments))


def test_system_install_creates_admin_policy_service_timer_and_all_user_autostart(
    tmp_path: Path,
) -> None:
    paths = InstallPaths(
        policy_file=tmp_path / "etc" / "user_updater" / "project.yaml",
        state_dir=tmp_path / "var" / "lib" / "user_updater",
        unit_dir=tmp_path / "systemd" / "system",
        autostart_dir=tmp_path / "xdg" / "autostart",
        log_file=tmp_path / "log" / "updater.log",
        executable=Path("/usr/local/bin/user-updater"),
    )
    systemd = FakeSystemd()
    installer = SystemInstaller(paths, systemd=systemd)

    installer.install()

    service = (paths.unit_dir / "user-updater.service").read_text(encoding="utf-8")
    timer = (paths.unit_dir / "user-updater.timer").read_text(encoding="utf-8")
    autostart = (paths.autostart_dir / "user-updater.desktop").read_text(encoding="utf-8")
    policy = load_policy(paths.policy_file)
    assert "ExecStart=/usr/local/bin/user-updater tick --system" in service
    assert "OnUnitActiveSec=1min" in timer
    assert "Exec=/usr/local/bin/user-updater gui --system" in autostart
    assert policy.updates.run_system is True
    assert (paths.state_dir / "users").is_dir()
    assert (paths.state_dir / "locks").is_dir()
    assert paths.log_file.parent.is_dir()
    assert systemd.calls[-1] == ["enable", "--now", "user-updater.timer"]


def test_system_reinstall_preserves_admin_policy_and_uninstall_preserves_data(
    tmp_path: Path,
) -> None:
    paths = InstallPaths(
        policy_file=tmp_path / "etc" / "project.yaml",
        state_dir=tmp_path / "var" / "state",
        unit_dir=tmp_path / "systemd",
        autostart_dir=tmp_path / "autostart",
        log_file=tmp_path / "log" / "updater.log",
        executable=Path("/usr/local/bin/user-updater"),
    )
    systemd = FakeSystemd()
    installer = SystemInstaller(paths, systemd=systemd)
    paths.policy_file.parent.mkdir(parents=True)
    paths.policy_file.write_text(
        "version: 1\ninteraction:\n  allow_deferral: false\n", encoding="utf-8"
    )
    paths.state_dir.mkdir(parents=True)
    (paths.state_dir / "keep.log").write_text("previous run", encoding="utf-8")

    installer.install()
    installer.uninstall()

    assert load_policy(paths.policy_file).interaction.allow_deferral is False
    assert (paths.state_dir / "keep.log").exists()
    assert not (paths.unit_dir / "user-updater.service").exists()
    assert not (paths.autostart_dir / "user-updater.desktop").exists()


def test_user_install_defaults_to_user_tools_only(tmp_path: Path) -> None:
    paths = UserInstallPaths(
        home=tmp_path / "alice",
        executable=Path("/home/alice/.local/bin/user-updater"),
    )
    systemd = FakeSystemd()
    UserInstaller(paths, systemd=systemd).install()

    policy = load_policy(paths.policy_file)
    service = paths.unit_dir / "user-updater.service"
    autostart = paths.autostart_dir / "user-updater.desktop"

    assert policy.updates.run_system is False
    assert policy.updates.run_user_tools is True
    assert "tick --user" in service.read_text(encoding="utf-8")
    assert "gui --user" in autostart.read_text(encoding="utf-8")


def test_user_install_escapes_spaces_in_systemd_and_desktop_paths(tmp_path: Path) -> None:
    paths = UserInstallPaths(
        home=tmp_path / "alice",
        executable=Path("/home/alice smith/.local/bin/user-updater"),
    )
    UserInstaller(paths, systemd=FakeSystemd()).install()

    service = (paths.unit_dir / "user-updater.service").read_text(encoding="utf-8")
    autostart = (paths.autostart_dir / "user-updater.desktop").read_text(encoding="utf-8")

    assert r"/home/alice\x20smith/.local/bin/user-updater" in service
    assert 'Exec="/home/alice smith/.local/bin/user-updater" gui --user' in autostart
