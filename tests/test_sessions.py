from pathlib import Path
from types import SimpleNamespace

from user_updater.sessions import detect_graphical_users


def test_session_detection_uses_logind_session_properties(tmp_path: Path) -> None:
    home = tmp_path / "desktop-user"
    home.mkdir()
    responses = {
        ("loginctl", "list-sessions", "--no-legend"): "3 1001 alice seat0 tty2\n4 1002 bob - -\n",
        (
            "loginctl",
            "show-session",
            "3",
            "-p",
            "User",
            "-p",
            "Class",
            "-p",
            "Type",
            "-p",
            "State",
            "-p",
            "Remote",
            "-p",
            "Active",
        ): "User=1001\nClass=user\nType=wayland\nState=active\nRemote=no\nActive=no\n",
        (
            "loginctl",
            "show-session",
            "4",
            "-p",
            "User",
            "-p",
            "Class",
            "-p",
            "Type",
            "-p",
            "State",
            "-p",
            "Remote",
            "-p",
            "Active",
        ): "User=1002\nClass=user\nType=tty\nState=active\nRemote=no\nActive=yes\n",
    }

    def executor(command: list[str], **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(returncode=0, stdout=responses[tuple(command)], stderr="")

    def account_lookup(uid: int) -> SimpleNamespace:
        return SimpleNamespace(
            pw_name="alice",
            pw_uid=uid,
            pw_gid=uid,
            pw_dir=str(home),
        )

    users = detect_graphical_users(executor=executor, account_lookup=account_lookup)

    assert [(user.username, user.uid) for user in users] == [("alice", 1001)]
