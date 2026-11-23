from pathlib import Path
from xml.etree import ElementTree

from user_updater.installer import InstallPaths, SystemInstaller, UserInstaller, UserInstallPaths
from user_updater.paths import user_paths
from user_updater.policy import load_policy


class FakeSystemd:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def run(self, arguments: list[str], *, check: bool = True) -> None:
        self.calls.append([*arguments, f"check={check}"] if not check else list(arguments))


def test_system_autostart_applies_to_users_added_after_install(tmp_path: Path) -> None:
    paths = InstallPaths(
        policy_file=tmp_path / "etc" / "user-updater" / "project.yaml",
        state_dir=tmp_path / "var" / "lib" / "user-updater",
        unit_dir=tmp_path / "systemd" / "system",
        autostart_dir=tmp_path / "etc" / "xdg" / "autostart",
        log_file=tmp_path / "log" / "updater.log",
        executable=Path("/usr/local/bin/user-updater"),
        polkit_action_file=tmp_path / "polkit" / "org.userupdater.policy",
    )
    SystemInstaller(paths, systemd=FakeSystemd()).install()

    global_entry = paths.autostart_dir / "user-updater.desktop"
    future_user_home = tmp_path / "home" / "added-after-install"
    future_user_home.mkdir(parents=True)

    assert "Exec=/usr/local/bin/user-updater gui --system" in global_entry.read_text(
        encoding="utf-8"
    )
    assert not (future_user_home / ".config" / "autostart" / global_entry.name).exists()


def test_system_install_creates_admin_policy_service_timer_and_global_autostart(
    tmp_path: Path,
) -> None:
    paths = InstallPaths(
        policy_file=tmp_path / "etc" / "user-updater" / "project.yaml",
        state_dir=tmp_path / "var" / "lib" / "user-updater",
        unit_dir=tmp_path / "systemd" / "system",
        autostart_dir=tmp_path / "xdg" / "autostart",
        log_file=tmp_path / "log" / "updater.log",
        executable=Path("/usr/local/bin/user-updater"),
        polkit_action_file=tmp_path / "polkit" / "org.userupdater.policy",
    )
    systemd = FakeSystemd()
    installer = SystemInstaller(paths, systemd=systemd)

    installer.install()

    service = (paths.unit_dir / "user-updater.service").read_text(encoding="utf-8")
    timer = (paths.unit_dir / "user-updater.timer").read_text(encoding="utf-8")
    autostart = (paths.autostart_dir / "user-updater.desktop").read_text(encoding="utf-8")
    polkit_action = paths.polkit_action_file.read_text(encoding="utf-8")
    ElementTree.fromstring(polkit_action)
    policy = load_policy(paths.policy_file)
    assert "ExecStart=/usr/local/bin/user-updater tick --system" in service
    assert "OnUnitActiveSec=1min" in timer
    assert "Exec=/usr/local/bin/user-updater gui --system" in autostart
    assert "Icon=system-software-update" in autostart
    assert (
        '<annotate key="org.freedesktop.policykit.exec.path">'
        "/usr/local/bin/user-updater-admin</annotate>"
    ) in polkit_action
    assert "<allow_active>auth_admin_keep</allow_active>" in polkit_action
    assert "<allow_any>no</allow_any>" in polkit_action
    assert policy.updates.run_system is True
    assert (paths.state_dir / "users").is_dir()
    assert (paths.state_dir / "locks").is_dir()
    for phase in ("before", "after"):
        for context in ("builder", "root"):
            assert (paths.policy_file.parent / "scripts" / phase / f"{context}.d").is_dir()
    assert paths.log_file.parent.is_dir()
    assert paths.log_file.parent.stat().st_mode & 0o777 == 0o755
    assert systemd.calls[-1] == ["enable", "--now", "user-updater.timer"]


def test_system_install_migrates_legacy_underscore_directories(tmp_path: Path) -> None:
    legacy_policy_dir = tmp_path / "etc" / "user_updater"
    legacy_state_dir = tmp_path / "var" / "lib" / "user_updater"
    legacy_policy_dir.mkdir(parents=True)
    legacy_state_dir.mkdir(parents=True)
    (legacy_policy_dir / "project.yaml").write_text(
        "version: 1\ninteraction:\n  allow_deferral: false\n", encoding="utf-8"
    )
    (legacy_state_dir / "state.json").write_text("preserve me", encoding="utf-8")
    paths = InstallPaths(
        policy_file=tmp_path / "etc" / "user-updater" / "project.yaml",
        state_dir=tmp_path / "var" / "lib" / "user-updater",
        unit_dir=tmp_path / "systemd",
        autostart_dir=tmp_path / "autostart",
        log_file=tmp_path / "log" / "updater.log",
        executable=Path("/usr/local/bin/user-updater"),
        polkit_action_file=tmp_path / "polkit" / "org.userupdater.policy",
        legacy_policy_dir=legacy_policy_dir,
        legacy_state_dir=legacy_state_dir,
    )

    SystemInstaller(paths, systemd=FakeSystemd()).install()

    assert load_policy(paths.policy_file).interaction.allow_deferral is False
    assert (paths.state_dir / "state.json").read_text(encoding="utf-8") == "preserve me"
    assert not legacy_policy_dir.exists()
    assert not legacy_state_dir.exists()


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
        polkit_action_file=tmp_path / "polkit" / "org.userupdater.policy",
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
    assert not paths.polkit_action_file.exists()


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
    assert "Icon=system-software-update" in autostart.read_text(encoding="utf-8")


def test_user_install_migrates_legacy_user_data_directories(tmp_path: Path) -> None:
    home = tmp_path / "alice"
    old_config = home / ".config" / "user_updater"
    old_state = home / ".local" / "state" / "user_updater"
    old_config.mkdir(parents=True)
    old_state.mkdir(parents=True)
    (old_config / "project.yaml").write_text(
        "version: 1\nupdates:\n  run_system: false\n", encoding="utf-8"
    )
    (old_config / "tool_preferences.json").write_text("prefs", encoding="utf-8")
    (old_state / "updater.log").write_text("log", encoding="utf-8")
    paths = UserInstallPaths(home=home, executable=Path("/home/alice/.local/bin/user-updater"))

    UserInstaller(paths, systemd=FakeSystemd()).install()
    resolved = user_paths(home)

    assert resolved.policy == home / ".config" / "user-updater" / "project.yaml"
    assert (resolved.policy.parent / "tool_preferences.json").read_text() == "prefs"
    assert (resolved.user_state_dir / "updater.log").read_text() == "log"
    assert not old_config.exists()
    assert not old_state.exists()


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
