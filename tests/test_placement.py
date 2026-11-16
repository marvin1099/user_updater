import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget

from user_updater.gui.placement import (
    WindowPlacement,
    _kdotool_move_arguments,
    is_kde_session,
)


def _application() -> QApplication:
    application = QApplication.instance()
    return application or QApplication([])


def test_kdotool_move_is_scoped_to_pid_and_window_title() -> None:
    assert _kdotool_move_arguments(1234, "User Updater Status", 700, 900) == [
        "--quiet",
        "search",
        "--all",
        "--pid",
        "1234",
        "--title",
        "User Updater Status",
        "windowmove",
        "700",
        "900",
    ]


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
