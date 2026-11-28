# User Updater

User Updater schedules Linux updates and provides a small Qt desktop client.

- **System install:** schedules system updates and each logged-in user's personal tools independently.
- **User-only install:** schedules personal tools for the current account; it never runs system updates.

Before a scheduled run, users get a short response window. They can start personal-tool updates or choose an allowed delay. If nobody responds, the run proceeds automatically. Closing the desktop client does not stop a worker.

## Requirements

- Linux with systemd; a local X11 or Wayland desktop is needed for the GUI.
- Python 3.11+ and [`uv`](https://docs.astral.sh/uv/).
- For system updates: [`topgrade`](https://github.com/topgrade-rs/topgrade), `sudo`, and `visudo`.

Install native tools and Topgrade separately with your distribution's package manager. Check the setup with `user-updater doctor` after installation.

## Installation

Clone the main repository to get started:

```console
git clone https://codeberg.org/marvin1099/user-updater.git
cd user-updater
```

### System-wide

From a trusted checkout, install the application in protected system paths, then create its systemd service and desktop entry:

```console
sudo env \
  UV_TOOL_DIR=/opt/user-updater/uv-tools \
  UV_TOOL_BIN_DIR=/usr/local/bin \
  uv tool install .
sudo user-updater install --system
```

The installer creates a system timer, an administrator policy at `/etc/user-updater/project.yaml`, shared state and logs, and a global desktop entry for existing and future users. Reinstalling preserves the policy and migrates older underscore-named data directories. **Admin settings** asks for polkit authorization; the desktop application itself remains unprivileged.

> System mode runs trusted installed code with elevated privileges. The installation must be root-owned and not writable by group or other users. Do not install it from an untrusted checkout.

### User-only

```console
uv tool install .
user-updater install --user
```

This creates a `systemd --user` timer and a desktop entry. The user service manager must be running, as it normally is during a desktop login. Settings are stored in `~/.config/user-updater/project.yaml`; personal-tool logs are kept in the user's state directory.

## Defaults

| Task | Schedule | Response window |
| --- | --- | --- |
| System updates | Thursday and Sunday at 03:00 local time | 5 minutes; no response means proceed |
| Personal tools | Thursday and Sunday at 18:00 local time | 5 minutes; no response means proceed |

Schedules and response windows can be changed in **Admin settings** or **User settings**.

- **Internet:** the connection must be stable for 60 seconds before a prompt or update. The worker checks again before commands start; a pending response is kept while it waits.
- **Rescheduling:** personal-tool delays default to 1, 4, or 24 hours, up to 48 hours total. System delays default to 24 hours and cannot pass the next system schedule. Administrators can disable or adjust either policy.
- **Retries:** up to two automatic retries, 15 minutes apart. User-requested retries are disabled by default and can be limited separately for each task.
- **Personal tools:** administrators set the allow-list; users can opt out of allowed tools. Custom user scripts are off by default and always run as their owner.

System updates run through Topgrade under a short-lived builder account. Its passwordless sudo is restricted to the detected package manager and Flatpak by default. Firmware updates are handled separately when needed, so the builder does not receive broader privileges. Administrators can optionally configure before/after hooks under `/etc/user-updater/scripts/`; root hooks run as root, so enable them only for trusted scripts.

## Configuration and desktop client

The checked-in [`project.yaml`](project.yaml) provides the defaults. System policy is stored at `/etc/user-updater/project.yaml`; user-only policy is stored at `~/.config/user-updater/project.yaml`. Validate a policy with:

```console
user-updater validate-policy /etc/user-updater/project.yaml
```

The compact window shows task status and response controls. **Full details** provides output, retry controls, tool preferences, run history, and log access. Admin settings also includes a testing section; simulated runs never invoke update commands.

Per-user state and personal-tool output are readable only by their owner. The shared system log is readable by local users but writable only by root. Run history keeps up to 20 metadata-only summaries per task. If a worker is interrupted during an update, the next tick marks that run failed rather than replaying a potentially partial update.

Automatic window placement is enabled by default. KDE Plasma Wayland can use optional `kdotool`; compositors supporting `wlr-layer-shell` can use the optional native bridge. Otherwise, placement is left to the desktop compositor.

## Commands

Run these as `user-updater <command>`:

| Purpose | System-wide | User-only |
| --- | --- | --- |
| Show status or history | `status --system`, `history --system` | `status --user`, `history --user` |
| View or export logs | `logs --system` (root only) | `logs --user` |
| Open the desktop client | `gui --system` | `gui --user` |
| Request a retry | `retry --system` (polkit) | `retry --user` |
| Start system updates immediately | `run-now --system` (root only) | — |
| Start personal-tool updates immediately | `run-user-tools-now --system` (root only; all active users) | `run-now --user` (this user's tools) |
| Run one scheduler check | `tick --system` (normally systemd) | — |
| Install or remove integration | `install --system`, `uninstall --system` | `install --user`, `uninstall --user` |

Add `--output FILE` to export logs. Add `--purge` to uninstall to also remove the policy and saved state. Other useful commands are `user-updater doctor` and `user-updater demo`.

`run-user-tools-now --system` starts personal-tool updates for every active local desktop user. It follows the administrator's tool allow-list and internet-stability settings.

## Development

```console
uv sync --all-groups
QT_QPA_PLATFORM=offscreen uv run pytest -q
uv run ruff check src tests setup.py
uv build
```

The tests mock system boundaries; the demo uses simulated state and never runs package managers or requests administrator privileges. See [reliability testing](docs/reliability-testing.md) for the VM checklist.

## License

GNU Affero General Public License v3. See [`LICENSE`](LICENSE).
