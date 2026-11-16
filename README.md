# User Updater

User Updater is a Python and Qt application for scheduled Linux updates. A machine-wide installation schedules system updates separately from each logged-in user's personal tools. The two jobs have independent schedules and response prompts, and run concurrently when both are due. A user-only installation updates only the current user's tools.

Before each scheduled update, the desktop client gives users a response window. A user may start immediately or request one of the reschedules allowed by administrator policy. If nobody responds, the update proceeds automatically. The compact frameless window can expand to read-only system/user output tabs and a recent-run history tab, with elapsed and next-run times. Failed tasks can be retried from the details view; system retries require administrator authorization. Closing either view never cancels a worker.

## Requirements

- Linux with systemd and a local X11 or Wayland desktop for the Qt client
- Python 3.11 or newer
- [`uv`](https://docs.astral.sh/uv/) for installation and development
- [`topgrade`](https://github.com/topgrade-rs/topgrade) installed separately for machine-wide update runs
- `sudo` and `visudo` for the temporary system-update builder account

The application does not install operating-system packages for you. Install `topgrade` and the required native tools using your distribution's package manager, then check the environment with `user-updater doctor`.

## Installation

### Machine-wide (administrator)

From a trusted checkout of this repository, install the Python application system-wide and then create its systemd integration:

```console
sudo uv tool install --system .
sudo user-updater install --system
```

The install creates a persistent systemd timer and service, an administrator policy at `/etc/user_updater/project.yaml`, shared state under `/var/lib/user_updater`, rotating logs under `/var/log/user-updater`, and a system-wide XDG autostart entry in `/etc/xdg/autostart`. That global entry is picked up at graphical login by existing users and users added later; adding a user does not require reinstalling the updater or copying a per-user autostart file. It preserves an existing policy on reinstall. The desktop client's **Admin settings** action opens a polkit authentication prompt before showing settings; only the narrow policy-writing command runs with administrator privileges.

System mode requires a root-owned, non-group/world-writable Python installation. This avoids running privileged update code from a user-writable checkout. Do not install this mode from untrusted source.

### User-only

For a personal install that never performs machine-wide package updates:

```console
uv tool install .
user-updater install --user
```

This creates a `systemd --user` timer and a per-user desktop autostart entry. Its default policy disables system updates and enables configured user-tool updates. The user manager must be running for the timer to fire; a normal desktop login starts it.

### Development checkout

```console
uv sync --all-groups
uv run user-updater doctor
uv run user-updater demo
```

The demo uses simulated state and never invokes package managers or requests administrator privileges.

## Administrator policy

The checked-in [`project.yaml`](project.yaml) is the default policy. System installation copies its defaults to `/etc/user_updater/project.yaml` only when no policy exists. Validate a policy with:

```console
user-updater validate-policy /etc/user_updater/project.yaml
```

Default behavior:

- Schedule: Thursday and Sunday at 03:00 local time.
- Response window: 5 minutes. No response means proceed.
- Personal tool updates: independently due every 24 hours per user, with a 5-minute response window. Missed runs remain due and are caught up when that user next has a desktop session.
- Reschedule choices: 1, 4, or 24 hours; at most two reschedules per scheduled run.
- System updates run through `topgrade` under a short-lived, non-login builder account. By default, its passwordless sudo rule is limited to the detected native package-manager executable.
- Active local graphical users can have their configured per-user tools updated separately, without administrator privileges. Their state is stored independently by UID.
- Built-in user tools are followed by executable custom hooks from `~/.config/user_updater/tools.d`. Hooks run as their owner, never as root; the administrator can disable hooks with `updates.allow_custom_user_tools: false`.

Set `interaction.allow_deferral: false` to remove rescheduling while retaining the response window. `interaction.deferral_options_hours` and `interaction.max_deferrals_per_run` can further limit rescheduling. `security.builder_sudo_mode: unrestricted` grants the temporary builder unrestricted passwordless sudo for the duration of the run; the default is the restricted `package-manager` mode.

Unknown policy keys and invalid values are rejected. User-mode execution always disables system updates even if a local policy attempts to enable them.

The minimal desktop view has separate controls for system and personal-tool updates. **Full details** opens System updates, User tools, and Run history tabs, with read-only output, elapsed time, and the next scheduled run. The owner may retry a failed personal-tool run; a failed system run offers retry only after a polkit authorization prompt.

### Wayland window placement

- On KDE Plasma Wayland, the client uses [`kdotool`](https://github.com/jinliu/kdotool), when installed, to request bottom-right placement through KWin. It is an optional desktop utility; without it, Qt's normal best-effort placement is used.
- On other Wayland compositors that advertise `wlr-layer-shell`, an optional native LayerShellQt bridge anchors both views at the bottom-right with a 20-pixel margin. This uses a top-layer surface rather than a normal task-switcher window; keyboard focus remains on-demand. The bridge is built when the build machine has CMake, Qt 6 Widgets, Python development headers, and LayerShellQt development files. It is enabled only when its Qt major/minor version matches PySide6 at runtime. If the native development files are absent or versions mismatch, the application uses normal Qt windows.
- Compositors without layer-shell support retain their normal window-placement policy. The updater's update, admin, and user-hook behavior is independent of these display integrations.

## Commands

| Command | Purpose |
| --- | --- |
| `user-updater status --system` | Show machine-wide update state |
| `user-updater retry --system` | Retry a failed machine-wide run after polkit authorization |
| `user-updater history --system` | Show recent system and per-user run summaries |
| `user-updater logs --system` | Print machine-wide logs (root only); add `--output FILE` to export |
| `user-updater gui --system` | Open the machine-wide desktop client |
| `user-updater tick --system` | Run one scheduler check; normally invoked by systemd |
| `user-updater status --user` | Show this user's update state |
| `user-updater retry --user` | Request an owner-authorized retry of failed personal-tool updates |
| `user-updater history --user` | Show recent personal-tool run summaries |
| `user-updater logs --user` | Print this user's logs; add `--output FILE` to export |
| `user-updater gui --user` | Open this user's desktop client |
| `user-updater demo` | Preview the Qt flow with simulated updates |
| `user-updater doctor` | Check runtime dependencies |
| `user-updater uninstall --system` | Remove systemd/autostart integration, preserving policy and state |
| `user-updater uninstall --system --purge` | Also remove the policy and stored state |

Uninstall the user integration with `user-updater uninstall --user`; add `--purge` to remove its local policy and state as well.

Each task keeps the latest 20 metadata-only run summaries; output stays in the rotating log rather than being duplicated in history. Machine-wide logs at `/var/log/user-updater/updater.log` are root-only through the CLI, while user-only logs are at `~/.local/state/user_updater/updater.log`. Log files rotate through up to five 10 MiB backups. The application uses separate locks and state for system and per-user tasks, and cleans abandoned temporary builder accounts and sudoers entries before starting a later system update.

If a worker is interrupted while its state says `running`, the next scheduler tick records the run as failed, preserves its run ID and start time, and advances to the next normal scheduled occurrence. It does not immediately replay an update that may have partially changed the system. System output stays in the root-only log; personal-tool output remains separately inspectable by its owner. Invalid persisted state is rejected rather than interpreted as permission to run an update. See [reliability testing](docs/reliability-testing.md) for the VM checklist.

## Tests and lint

The suite mocks account, systemd, updater, and desktop-session boundaries; Qt tests use the offscreen platform and do not alter the host system:

```console
QT_QPA_PLATFORM=offscreen uv run pytest -q
uv run ruff check src tests setup.py
uv build
```

## License

Licensed under the GNU Affero General Public License v3. See [`LICENSE`](LICENSE).
