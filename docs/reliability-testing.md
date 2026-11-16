# Reliability testing

## Automated checks

GitHub Actions runs Ruff and the offscreen Qt/unit suite on Python 3.11 and 3.13. A separate job builds the sdist and wheel, installs the wheel into a clean environment outside the checkout, checks the installed CLI and GUI placement module, and verifies the generated system and user service/timer units with `systemd-analyze verify`.

Run the same local checks with:

```console
uv sync --all-groups
uv run ruff check src tests setup.py
QT_QPA_PLATFORM=offscreen uv run pytest -q
uv build
```

The service tests model a process restart by persisting a `running` state, constructing a fresh service, and ticking it again. Recovery must mark the old run failed, preserve its identity, avoid invoking the workflow again, and wait until the next normal due time. Personal-tool state may retain its recent output; system output stays in the root-only log and is cleared from shared state. This is deliberately not an automatic immediate retry: a package update may have applied only part of its changes.

## KDE Wayland GUI smoke test

Use a private KDE test session where possible, rather than opening or moving windows on a user's active desktop. Launch the safe demo (`user-updater demo`), wait for **User Updater Status**, and verify the window appears at bottom right. The KDE path should use the PID-and-title-scoped `kdotool` call; verify moving the demo window does not affect other applications. The demo only writes temporary simulated state and never invokes package tools.

The LayerShellQt bridge requires a separate compositor advertising `wlr-layer-shell`; KDE is intentionally tested through `kdotool`. On a wlroots desktop, confirm bottom/right anchoring, keyboard focus when requested, and normal Qt fallback when the bridge is missing or built against a mismatched Qt minor version. Do not treat a successful build or a simulated `kdotool --dry-run` as proof of live placement.

## Disposable VM installation checklist

Run privileged installation checks only in a disposable VM snapshot. Do not install the system service on a development workstation for this test. The system-mode installation runs as root, so keep the VM's update policy safe:

1. Install the application from a trusted, root-owned build and run `user-updater doctor`.
2. Before installing, set both `updates.run_system` and `updates.run_user_tools` to `false` in the VM's test policy. This lets you verify systemd integration and state transitions without invoking package managers or user hooks.
3. Run `user-updater install --system`; verify the timer and service with `systemctl status user-updater.timer` and `systemctl cat user-updater.service`. Confirm the single system-wide entry exists at `/etc/xdg/autostart/user-updater.desktop`.
4. Configure a short response window and a schedule a few minutes ahead. Verify the response UI, a permitted deferral, and the no-response path. Since updates are disabled, the workflow should finish without package changes.
5. Add a new VM user **after** installation, then start that user's graphical session. Confirm the system-wide autostart launches the response/status client without reinstalling the updater or creating a per-user autostart file.
6. Verify system and user state remain separate, reinstall preserves the administrator policy, and ordinary uninstall preserves policy/state. Verify `--purge` only on a disposable snapshot.
7. For a failure path, use a disposable **user-only** VM account and a user-owned executable test hook that exits nonzero; confirm it runs as that user and cannot affect the machine-wide update path.
8. Restore the VM snapshot after testing.

Do not simulate a crash by killing a real package manager on a working machine. Crash recovery is covered deterministically by the automated service/coordinator tests; a VM interruption experiment should use a snapshot and a controlled fake updater only.
