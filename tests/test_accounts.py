from pathlib import Path
from types import SimpleNamespace

from user_updater.accounts import BuilderAccount, BuilderAccountManager, LinuxAccountBackend
from user_updater.policy import load_policy


class FakeAccountBackend:
    package_manager = "/usr/bin/pacman"
    flatpak: str | None = None

    def __init__(self) -> None:
        self.created: list[tuple[str, Path]] = []
        self.removed: list[str] = []
        self.sudoers: dict[Path, str] = {}
        self.managed: list[BuilderAccount] = []
        self.builder_group_ids: tuple[int, ...] = ()

    def create_user(self, name: str, home: Path) -> None:
        self.created.append((name, home))
        home.mkdir(parents=True)

    def user_ids(self, _name: str) -> tuple[int, int]:
        return 4242, 4242

    def supplementary_group_ids(self, _name: str) -> tuple[int, ...]:
        return self.builder_group_ids

    def managed_accounts(self, _home_root: Path, _sudoers_root: Path) -> list[BuilderAccount]:
        return list(self.managed)

    def write_sudoers(self, path: Path, content: str) -> None:
        self.sudoers[path] = content

    def delete_user(self, name: str) -> None:
        self.removed.append(name)

    def remove_sudoers(self, path: Path) -> None:
        self.sudoers.pop(path, None)
        path.unlink(missing_ok=True)


def test_temporary_builder_gets_limited_passwordless_package_manager_access(
    tmp_path: Path,
) -> None:
    backend = FakeAccountBackend()
    manager = BuilderAccountManager(
        home_root=tmp_path / "builders",
        sudoers_root=tmp_path / "sudoers",
        policy=load_policy(),
        backend=backend,
    )

    account = manager.create()
    sudoers = backend.sudoers[account.sudoers_path]

    assert account.name.startswith("uupd-")
    assert account.home.is_dir()
    assert "NOPASSWD" in sudoers
    assert "/usr/bin/pacman" in sudoers
    assert "ALL) NOPASSWD: ALL" not in sudoers

    manager.remove(account)

    assert backend.removed == [account.name]
    assert account.home.exists() is False
    assert account.sudoers_path not in backend.sudoers


def test_temporary_builder_gets_only_the_configured_supplementary_groups(
    tmp_path: Path,
) -> None:
    backend = FakeAccountBackend()
    backend.builder_group_ids = (937,)
    manager = BuilderAccountManager(
        home_root=tmp_path / "builders",
        sudoers_root=tmp_path / "sudoers",
        policy=load_policy(),
        backend=backend,
    )

    account = manager.create()

    assert account.supplementary_group_ids == (937,)
    manager.remove(account)
    assert backend.removed == [account.name]


def test_linux_builder_user_is_created_with_docker_group_membership(
    tmp_path: Path, monkeypatch
) -> None:
    calls: list[list[str]] = []
    account_info = SimpleNamespace(pw_uid=4242, pw_gid=4242)
    monkeypatch.setattr(
        "user_updater.accounts.shutil.which",
        lambda name, path=None: "/usr/bin/docker" if name == "docker" else None,
    )
    monkeypatch.setattr(
        "user_updater.accounts.grp.getgrnam",
        lambda name: SimpleNamespace(gr_name=name, gr_gid=937),
    )
    monkeypatch.setattr("user_updater.accounts.pwd.getpwnam", lambda _name: account_info)
    monkeypatch.setattr("user_updater.accounts.os.chown", lambda *_args: None)
    monkeypatch.setattr(
        "user_updater.accounts.os.getgrouplist",
        lambda _name, _gid: [4242, 937],
    )
    monkeypatch.setattr(
        "user_updater.accounts.subprocess.run",
        lambda command, **_kwargs: calls.append(command),
    )

    backend = LinuxAccountBackend()
    backend.create_user("uupd-0123456789", tmp_path / "builder")

    assert calls[0][0] == "useradd"
    assert calls[0][calls[0].index("--groups") + 1] == "docker"
    assert backend.supplementary_group_ids("uupd-0123456789") == (937,)


def test_unrestricted_builder_sudo_requires_explicit_admin_policy(tmp_path: Path) -> None:
    config = tmp_path / "project.yaml"
    config.write_text(
        "version: 1\nsecurity:\n  builder_sudo_mode: unrestricted\n", encoding="utf-8"
    )
    backend = FakeAccountBackend()
    manager = BuilderAccountManager(
        home_root=tmp_path / "builders",
        sudoers_root=tmp_path / "sudoers",
        policy=load_policy(config),
        backend=backend,
    )

    account = manager.create()

    assert f"{account.name} ALL=(ALL:ALL) NOPASSWD: ALL" in backend.sudoers[account.sudoers_path]


def test_garuda_update_wrapper_is_preferred_over_pacman(monkeypatch) -> None:
    existing = {"/usr/bin/garuda-update", "/usr/bin/pacman"}
    monkeypatch.setattr(Path, "is_file", lambda self: str(self) in existing)

    assert LinuxAccountBackend().package_manager == "/usr/bin/garuda-update"


def test_temporary_builder_sudoers_allows_limited_paru_pacman_commands(
    tmp_path: Path,
) -> None:
    backend = FakeAccountBackend()
    backend.package_manager = "/usr/bin/garuda-update"
    backend.flatpak = "/usr/bin/flatpak"
    manager = BuilderAccountManager(
        home_root=tmp_path / "builders",
        sudoers_root=tmp_path / "sudoers",
        policy=load_policy(),
        backend=backend,
    )

    account = manager.create()

    sudoers = backend.sudoers[account.sudoers_path]
    assert sudoers.splitlines() == [
        f"{account.name} ALL=(root) NOPASSWD: /usr/bin/pacman -S *, "
        "/usr/bin/pacman --sync *, "
        "/usr/bin/pacman -Syu --noconfirm, /usr/bin/pacman -Scc --noconfirm, "
        "/usr/bin/pacman -U *, /usr/bin/pacman --upgrade *, "
        "/usr/bin/flatpak, /usr/bin/flatpak *",
    ]
    assert "/usr/bin/garuda-update" not in sudoers
    assert "UPDATE_AUR" not in sudoers
    assert "SETENV" not in sudoers


def test_non_garuda_package_manager_does_not_get_environment_overrides(
    tmp_path: Path,
) -> None:
    backend = FakeAccountBackend()
    manager = BuilderAccountManager(
        home_root=tmp_path / "builders",
        sudoers_root=tmp_path / "sudoers",
        policy=load_policy(),
        backend=backend,
    )

    account = manager.create()
    sudoers = backend.sudoers[account.sudoers_path]

    assert "Defaults!" not in sudoers
    assert "UPDATE_AUR" not in sudoers
    assert "SETENV" not in sudoers


def test_stale_builder_and_orphan_sudoers_are_removed(tmp_path: Path) -> None:
    backend = FakeAccountBackend()
    home_root = tmp_path / "builders"
    sudoers_root = tmp_path / "sudoers"
    stale_name = "uupd-abcdef0123"
    stale_home = home_root / stale_name
    stale_home.mkdir(parents=True)
    stale = BuilderAccount(
        stale_name,
        4242,
        4242,
        stale_home,
        sudoers_root / f"user-updater-{stale_name}",
    )
    backend.managed.append(stale)
    manager = BuilderAccountManager(home_root, sudoers_root, load_policy(), backend)

    manager.cleanup_stale()

    assert backend.removed == [stale_name]
    assert stale_home.exists() is False


def test_orphan_sudoers_entry_is_removed_even_if_its_account_is_gone(
    tmp_path: Path,
) -> None:
    backend = FakeAccountBackend()
    home_root = tmp_path / "builders"
    sudoers_root = tmp_path / "sudoers"
    sudoers_root.mkdir(parents=True)
    orphan_name = "uupd-abcdef0123"
    orphan_file = sudoers_root / f"user-updater-{orphan_name}"
    orphan_file.write_text(f"{orphan_name} ALL=(root) NOPASSWD: /usr/bin/pacman\n")
    backend.sudoers[orphan_file] = orphan_file.read_text()
    manager = BuilderAccountManager(home_root, sudoers_root, load_policy(), backend)

    manager.cleanup_stale()

    assert orphan_file.exists() is False
    assert orphan_file not in backend.sudoers
