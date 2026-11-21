# User Updater

User Updater is a Python and Qt application for scheduled Linux updates. A machine-wide installation schedules system updates separately from each logged-in user's personal tools. The jobs have independent schedules and run concurrently when both are due; users can respond to their personal-tool prompt, while system updates follow administrator settings. A user-only installation updates only the current user's tools.

Before a personal-tool update, the desktop client gives the user a response window. The user may start it immediately or request one of the administrator-allowed reschedules; no response means it proceeds automatically. System updates are informational to users and run on the administrator's schedule without user reschedule/start controls. The compact frameless window can expand to read-only system/user output tabs and a recent-run history tab, with elapsed and next-run times. Failed tasks can be retried from the details view within the configured limit; system retries require administrator authorization. Closing either view never cancels a worker.

## Requirements

- Linux with systemd and a local X11 or Wayland desktop for the Qt client
- Python 3.11 or newer
- [`uv`](https://docs.astral.sh/uv/) for installation and development
- [`topgrade`](https://github.com/topgrade-rs/topgrade) installed separately for machine-wide update runs
- `sudo` and `visudo` for the temporary system-update builder account

The application does not install operating-system packages for you. Install `topgrade` and the required native tools using your distribution's package manager, then check the environment with `user-updater doctor`.

## Installation

### Machine-wide (administrator)

From a trusted checkout of this repository, install the Python application system-wide and then create its systemd integration. `uv tool install` has no `--system` option; these variables place the isolated environment under `/opt` and its executable in `/usr/local/bin`:

```console
sudo env \
  UV_TOOL_DIR=/opt/user-updater/uv-tools \
  UV_TOOL_BIN_DIR=/usr/local/bin \
  uv tool install .
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
- Personal-tool reschedule choices: 1, 4, or 24 hours; at most two reschedules per scheduled run.
- Automatic retries: up to 2 additional attempts per scheduled run, 15 minutes apart, independently for system updates and personal tools.
- User-requested retries: disabled by default for both task types. Administrators can allow 0–5 per scheduled run; system retry requests always go through polkit.
- System updates run through Topgrade's `system` and `flatpak` steps under a short-lived, non-login builder account; the system Flatpak installation is updated with sudo, while unrelated Topgrade steps such as containers are not run as that builder. The updater creates a private `~/.config/topgrade.toml` with `[misc] assume_yes = true`, Garuda's `--noconfirm` wrapper argument when applicable, and `[flatpak] use_sudo = true`, and invokes Topgrade with `--only system flatpak`. The user's Topgrade config is not reused. By default, the builder's passwordless sudo rule is limited to the detected native system-update executable (including Garuda's `garuda-update` wrapper when present) and the installed Flatpak CLI.
- Active local graphical users can have their configured per-user tools updated separately, without administrator privileges. Their state is stored independently by UID.
- The administrator's `updates.user_tools` list is the hard allow-list for personal tools. Users may opt out of individual allowed tools; all allowed tools start selected until a user saves preferences. Administrators can disable personal-tool updates entirely with `updates.run_user_tools: false`.
- Built-in user tools can be followed by executable custom hooks from `~/.config/user_updater/tools.d`. Users can enable or disable these scripts in **Full details → User tools**; the administrator can disable them globally with `updates.allow_custom_user_tools: false`. Scripts run as their owner, never as root.

Retry policy is configured independently under `retry.system` and `retry.user_tools`. Each section accepts `automatic_attempts` (0–5), `delay_minutes` (1–1440), and `user_manual_attempts` (0–5). Set `interaction.allow_deferral: false` to remove rescheduling while retaining the response window; it applies to personal-tool updates. Users cannot defer or start machine-wide system updates, which proceed according to administrator policy. `interaction.deferral_options_hours` and `interaction.max_deferrals_per_run` can further limit personal-tool rescheduling. `security.builder_sudo_mode: unrestricted` grants the temporary builder unrestricted passwordless sudo for the duration of the run; the default is the restricted `package-manager` mode.

Unknown policy keys and invalid values are rejected. User-mode execution always disables system updates even if a local policy attempts to enable them.

The minimal desktop view shows machine-wide system updates without controls. In **Full details**, users can request a system retry only when the administrator allows it, and that request requires polkit authorization. The details view opens System updates, User tools, and Run history tabs, with read-only output, elapsed time, and the next scheduled run. System output shows a read-only, bounded preview (up to 200 recent lines); full system logs remain root-only. Users can toggle their allowed personal tools in the User tools tab and may retry failed personal-tool runs only within the administrator's configured limit.

### Wayland window placement

- On KDE Plasma Wayland, the client uses [`kdotool`](https://github.com/jinliu/kdotool), when installed, to request bottom-right placement through KWin. It is an optional desktop utility; without it, Qt's normal best-effort placement is used.
- On other Wayland compositors that advertise `wlr-layer-shell`, an optional native LayerShellQt bridge anchors both views at the bottom-right with a 20-pixel margin. This uses a top-layer surface rather than a normal task-switcher window; keyboard focus remains on-demand. The bridge is built when the build machine has CMake, Qt 6 Widgets, Python development headers, and LayerShellQt development files. It is enabled only when its Qt major/minor version matches PySide6 at runtime. If the native development files are absent or versions mismatch, the application uses normal Qt windows.
- Compositors without layer-shell support retain their normal window-placement policy. The updater's update, admin, and user-hook behavior is independent of these display integrations.

## Commands

| Command | Purpose |
| --- | --- |
| `user-updater status --system` | Show machine-wide update state |
| `user-updater run-now --system` | Start machine-wide system updates immediately after polkit authorization |
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

Each task keeps the latest 20 metadata-only run summaries; output is not duplicated in history. Machine-wide logs at `/var/log/user-updater/updater.log` are root-only through the CLI, while the desktop client can read the bounded system-output preview from shared state. User-only logs are at `~/.local/state/user_updater/updater.log`. Log files rotate through up to five 10 MiB backups. The application uses separate locks and state for system and per-user tasks, and cleans abandoned temporary builder accounts and sudoers entries before starting a later system update.

If a worker is interrupted while its state says `running`, the next scheduler tick records the run as failed, preserves its run ID and start time, and advances to the next normal scheduled occurrence. It does not automatically replay an interrupted update that may have partially changed the system. System output is written to the root-only log with only the bounded preview shared with users; personal-tool output remains inspectable by its owner. Invalid persisted state is rejected rather than interpreted as permission to run an update. See [reliability testing](docs/reliability-testing.md) for the VM checklist.

## Tests and lint

The suite mocks account, systemd, updater, and desktop-session boundaries; Qt tests use the offscreen platform and do not alter the host system:

```console
QT_QPA_PLATFORM=offscreen uv run pytest -q
uv run ruff check src tests setup.py
uv build
```

## License

Licensed under the GNU Affero General Public License v3. See [`LICENSE`](LICENSE).
