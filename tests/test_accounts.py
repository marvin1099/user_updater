from pathlib import Path

from user_updater.accounts import BuilderAccount, BuilderAccountManager
from user_updater.policy import load_policy


class FakeAccountBackend:
    package_manager = "/usr/bin/pacman"

    def __init__(self) -> None:
        self.created: list[tuple[str, Path]] = []
        self.removed: list[str] = []
        self.sudoers: dict[Path, str] = {}
        self.managed: list[BuilderAccount] = []

    def create_user(self, name: str, home: Path) -> None:
        self.created.append((name, home))
        home.mkdir(parents=True)

    def user_ids(self, _name: str) -> tuple[int, int]:
        return 4242, 4242

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
