# User Updater

User Updater is a Python and Qt application for scheduled Linux updates. A machine-wide installation schedules system updates separately from each logged-in user's personal tools. The two jobs have independent schedules and response prompts, and run concurrently when both are due. A user-only installation updates only the current user's tools.

Before each scheduled update, the desktop client gives users a response window. A user may start immediately or request one of the reschedules allowed by administrator policy. If nobody responds, the update proceeds automatically. The compact frameless window can expand to a two-tab, read-only output view with elapsed time. Closing either view never cancels a worker. The application requests bottom-right placement, but Wayland compositors may intentionally ignore client positioning requests.

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

The install creates a persistent systemd timer and service, an administrator policy at `/etc/user_updater/project.yaml`, shared state under `/var/lib/user_updater`, rotating logs under `/var/log/user-updater`, and an XDG autostart entry in `/etc/xdg/autostart` so every desktop user gets the response/status client. It preserves an existing policy on reinstall. The desktop client's **Admin settings** action opens a polkit authentication prompt before showing settings; only the narrow policy-writing command runs with administrator privileges.

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

The minimal desktop view has separate controls for system and personal-tool updates. **Full details** opens System updates and User tools tabs with read-only command output and elapsed time. On a Wayland desktop, final window placement remains compositor-controlled.

## Commands

| Command | Purpose |
| --- | --- |
| `user-updater status --system` | Show machine-wide update state |
| `user-updater gui --system` | Open the machine-wide desktop client |
| `user-updater tick --system` | Run one scheduler check; normally invoked by systemd |
| `user-updater status --user` | Show this user's update state |
| `user-updater gui --user` | Open this user's desktop client |
| `user-updater demo` | Preview the Qt flow with simulated updates |
| `user-updater doctor` | Check runtime dependencies |
| `user-updater uninstall --system` | Remove systemd/autostart integration, preserving policy and state |
| `user-updater uninstall --system --purge` | Also remove the policy and stored state |

Uninstall the user integration with `user-updater uninstall --user`; add `--purge` to remove its local policy and state as well.

Worker output is available through the system or user journal and a rotating log file (up to five 10 MiB backups). System logs are in `/var/log/user-updater/updater.log`; user-only logs are in `~/.local/state/user_updater/updater.log`. The application uses separate locks and state for system and per-user tasks, and cleans abandoned temporary builder accounts and sudoers entries before starting a later system update.

## Tests and lint

The suite mocks account, systemd, updater, and desktop-session boundaries; Qt tests use the offscreen platform and do not alter the host system:

```console
QT_QPA_PLATFORM=offscreen uv run pytest -q
uv run ruff check src tests
uv build
```

## License

Licensed under the GNU Affero General Public License v3. See [`LICENSE`](LICENSE).
