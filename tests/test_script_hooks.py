import os
from pathlib import Path

import pytest

from user_updater.script_hooks import ScriptHookError, ensure_hook_directories, hook_scripts


def _hook_root(tmp_path: Path) -> Path:
    root = tmp_path / "etc" / "user-updater" / "scripts"
    root.parent.mkdir(parents=True)
    ensure_hook_directories(root, owner_uid=os.getuid())
    return root


def test_hook_directories_are_created_for_each_phase_and_identity(tmp_path: Path) -> None:
    root = _hook_root(tmp_path)

    assert {
        path.relative_to(root).as_posix()
        for path in root.glob("*/*.d")
    } == {
        "before/builder.d",
        "before/root.d",
        "after/builder.d",
        "after/root.d",
    }
    assert all(path.stat().st_mode & 0o777 == 0o755 for path in root.glob("*/*.d"))


def test_hook_scripts_are_executable_trusted_and_sorted(tmp_path: Path) -> None:
    root = _hook_root(tmp_path)
    directory = root / "before" / "builder.d"
    first = directory / "10-prepare"
    second = directory / "20-cleanup"
    ignored = directory / "notes.txt"
    first.write_text("#!/bin/sh\n", encoding="utf-8")
    second.write_text("#!/bin/sh\n", encoding="utf-8")
    ignored.write_text("not executable\n", encoding="utf-8")
    first.chmod(0o755)
    second.chmod(0o755)
    (directory / "shortcut").symlink_to(first)

    assert hook_scripts(root, "before", "builder", owner_uid=os.getuid()) == (
        first,
        second,
    )


def test_writable_hook_directories_and_scripts_are_rejected(tmp_path: Path) -> None:
    root = _hook_root(tmp_path)
    directory = root / "after" / "root.d"
    script = directory / "unsafe"
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    script.chmod(0o755)

    directory.chmod(0o775)
    with pytest.raises(ScriptHookError, match="directory"):
        hook_scripts(root, "after", "root", owner_uid=os.getuid())

    directory.chmod(0o755)
    script.chmod(0o777)
    with pytest.raises(ScriptHookError, match="group/world-writable"):
        hook_scripts(root, "after", "root", owner_uid=os.getuid())


def test_builder_hooks_must_be_accessible_to_the_builder(tmp_path: Path) -> None:
    root = _hook_root(tmp_path)
    directory = root / "before" / "builder.d"
    directory.chmod(0o700)

    with pytest.raises(ScriptHookError, match="searchable"):
        hook_scripts(root, "before", "builder", owner_uid=os.getuid())
