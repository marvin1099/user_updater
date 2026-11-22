import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget

from user_updater.gui.placement import (
    WindowPlacement,
    _kdotool_work_area_move_arguments,
    is_kde_session,
)


def _application() -> QApplication:
    application = QApplication.instance()
    return application or QApplication([])


def test_kdotool_move_uses_kde_work_area_and_scopes_window() -> None:
    arguments = _kdotool_work_area_move_arguments(
        1234, 'User "Updater" Status', 20, "HDMI-A-1"
    )

    assert arguments[:3] == ["--quiet", "kwinscript", "--inline"]
    assert 'window.pid === 1234' in arguments[3]
    assert 'window.caption === "User \\"Updater\\" Status"' in arguments[3]
    assert 'output.name === "HDMI-A-1"' in arguments[3]
    assert (
        "workspace.clientArea(KWin.MaximizeArea, output, "
        "workspace.currentDesktopForScreen(output))"
    ) in arguments[3]
    assert "geometry.x = area.x + area.width - window.width - 20" in arguments[3]
    assert "geometry.y = area.y + area.height - window.height - 20" in arguments[3]
    assert "window.frameGeometry = geometry" in arguments[3]


def test_kde_detection_uses_desktop_environment_names(monkeypatch) -> None:
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE:Plasma")
    monkeypatch.setenv("XDG_SESSION_DESKTOP", "")
    monkeypatch.setenv("DESKTOP_SESSION", "")

    assert is_kde_session()

    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "sway")
    assert not is_kde_session()


def test_qt_fallback_positions_window_against_available_geometry() -> None:
    app = _application()
    window = QWidget()
    window.resize(300, 180)
    placement = WindowPlacement(window, title="Test Updater Window", margin=20)
    screen = app.primaryScreen()
    assert screen is not None
    available = screen.availableGeometry()

    placement.position_bottom_right()

    assert window.pos().x() == available.x() + available.width() - window.width() - 20
    assert window.pos().y() == available.y() + available.height() - window.height() - 20
    window.deleteLater()
