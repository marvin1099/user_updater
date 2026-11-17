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

The service tests model a process restart by persisting a `running` state, constructing a fresh service, and ticking it again. Recovery must mark the old run failed, preserve its identity, avoid invoking the workflow again, and wait until the next normal due time. The Topgrade workflow test also verifies that a missing builder config is created with `[misc] assume_yes = true` before Topgrade starts. System state exposes only a bounded read-only output preview; complete machine logs remain root-only. A normal failed run may be retried automatically according to the separate system/personal-tool policy, but an interrupted run is never automatically replayed because a package update may have applied only part of its changes.

## KDE Wayland GUI smoke test

Use a private KDE test session where possible, rather than opening or moving windows on a user's active desktop. Launch the safe demo (`user-updater demo`), wait for **User Updater Status**, and verify the window appears at bottom right inside KWin's `WorkArea`. The KDE path uses a PID-and-title-scoped KWin script through `kdotool`; verify moving the demo window does not affect other applications. The demo only writes temporary simulated state and never invokes package tools.

The LayerShellQt bridge requires a separate compositor advertising `wlr-layer-shell`; KDE is intentionally tested through `kdotool`. On a wlroots desktop, confirm bottom/right anchoring, 20px bottom/right margins, keyboard focus when requested, and normal Qt fallback when the bridge is missing or built against a mismatched Qt minor version. Do not treat a successful build or a simulated `kdotool --dry-run` as proof of live placement; inspect the compositor result or the live layer-shell protocol exchange.

## Disposable VM installation checklist

Run privileged installation checks only in a disposable VM snapshot. Do not install the system service on a development workstation for this test. The system-mode installation runs as root, so keep the VM's update policy safe:

1. Install the application from a trusted, root-owned build and run `user-updater doctor`.
2. Before installing, set `updates.run_system: false`, `updates.run_user_tools: true`, `updates.user_tools: []`, and `updates.allow_custom_user_tools: false` in the VM's test policy. This lets you exercise user response behavior without invoking package managers or user hooks.
3. Run `user-updater install --system`; verify the timer and service with `systemctl status user-updater.timer` and `systemctl cat user-updater.service`. Confirm the single system-wide entry exists at `/etc/xdg/autostart/user-updater.desktop`.
4. Configure a short response window and a personal-tool interval a few minutes ahead. Verify the system panel is read-only, the personal-tool panel offers only policy-allowed rescheduling, and no response causes the empty personal-tool workflow to complete without package changes.
5. Add a new VM user **after** installation, then start that user's graphical session. Confirm the system-wide autostart launches the response/status client without reinstalling the updater or creating a per-user autostart file.
6. Verify system and user state remain separate, reinstall preserves the administrator policy, and ordinary uninstall preserves policy/state. Verify `--purge` only on a disposable snapshot.
7. For a failure path, use a disposable **user-only** VM account and a user-owned executable test hook that exits nonzero; confirm it runs as that user and cannot affect the machine-wide update path.
8. Restore the VM snapshot after testing.

Do not simulate a crash by killing a real package manager on a working machine. Crash recovery is covered deterministically by the automated service/coordinator tests; a VM interruption experiment should use a snapshot and a controlled fake updater only.
