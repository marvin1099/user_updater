import pytest

from user_updater import admin_helper, cli


def test_admin_helper_exposes_only_authorize_write_and_run_actions(monkeypatch) -> None:
    monkeypatch.setattr(admin_helper, "_require_trusted_root_install", lambda: None)
    monkeypatch.setattr(cli, "_write_admin_policy", lambda: 12)
    monkeypatch.setattr(cli, "_run_system_now", lambda *, system_mode: 23)

    assert admin_helper.main(["authorize-settings"]) == 0
    assert admin_helper.main(["write-policy"]) == 12
    assert admin_helper.main(["run-now"]) == 23

    with pytest.raises(SystemExit):
        admin_helper.main(["tick"])


def test_admin_helper_requires_root_install_validation_for_every_action(monkeypatch) -> None:
    calls: list[bool] = []

    def require_trusted_root_install() -> None:
        calls.append(True)
        raise RuntimeError("not trusted")

    monkeypatch.setattr(admin_helper, "_require_trusted_root_install", require_trusted_root_install)

    assert admin_helper.main(["authorize-settings"]) == 1
    assert calls == [True]
