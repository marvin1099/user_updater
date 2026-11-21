import json
import os
from pathlib import Path

import pytest

from user_updater.tool_preferences import ToolPreferenceError, UserToolPreferenceStore


def test_missing_preferences_enable_all_tools_allowed_by_admin(tmp_path: Path) -> None:
    store = UserToolPreferenceStore(tmp_path / "preferences.json", owner_uid=os.getuid())

    assert store.load(("flatpak", "pipx", "cargo")) == ("flatpak", "pipx", "cargo")
    assert store.load_all(("flatpak", "pipx")).custom_tools is True


def test_user_can_toggle_tools_but_cannot_enable_an_admin_disabled_tool(
    tmp_path: Path,
) -> None:
    store = UserToolPreferenceStore(tmp_path / "preferences.json", owner_uid=os.getuid())
    admin_allowed = ("flatpak", "pipx", "cargo")

    selected = store.save(("cargo", "flatpak"), admin_allowed)

    assert selected == ("flatpak", "cargo")
    assert store.load(admin_allowed) == ("flatpak", "cargo")
    assert store.load(("flatpak", "pipx")) == ("flatpak",)
    assert store.path.stat().st_mode & 0o777 == 0o600


def test_custom_tools_preference_is_saved_and_capped_by_administrator(tmp_path: Path) -> None:
    store = UserToolPreferenceStore(tmp_path / "preferences.json", owner_uid=os.getuid())
    allowed = ("flatpak",)

    store.save(allowed, allowed, custom_tools=False)

    assert store.load_all(allowed).custom_tools is False
    assert store.load_all(allowed, admin_allows_custom=False).custom_tools is False


def test_legacy_tool_preferences_keep_the_previous_custom_script_default(tmp_path: Path) -> None:
    path = tmp_path / "preferences.json"
    path.write_text('{"version":1,"tools":["flatpak"]}', encoding="utf-8")
    path.chmod(0o600)
    store = UserToolPreferenceStore(path, owner_uid=os.getuid())

    assert store.load_all(("flatpak",)).custom_tools is True


def test_preferences_reject_unknown_or_malformed_tools(tmp_path: Path) -> None:
    path = tmp_path / "preferences.json"
    path.write_text(json.dumps({"version": 1, "tools": ["arbitrary-command"]}), encoding="utf-8")
    store = UserToolPreferenceStore(path, owner_uid=os.getuid())

    with pytest.raises(ToolPreferenceError):
        store.load(("flatpak",))


def test_preferences_refuse_symbolic_links(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    target.write_text('{"version":1,"tools":["flatpak"]}', encoding="utf-8")
    link = tmp_path / "preferences.json"
    link.symlink_to(target)
    store = UserToolPreferenceStore(link, owner_uid=os.getuid())

    with pytest.raises(ToolPreferenceError):
        store.load(("flatpak",))
