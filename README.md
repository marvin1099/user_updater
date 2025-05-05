## **user_updater**

A simple tool for automatic system updates using [`topgrade`](https://github.com/topgrade-rs/topgrade),
featuring a background systemd service and lightweight GUI to notify users during updates.

> Unix-like systems only.  
> macOS *may* work, but it’s not officially supported.

---

### Features

* Automatic system updates via [`topgrade`](https://github.com/topgrade-rs/topgrade)
* Desktop GUI notifications (using `yad`)
* Temporary passwordless `builder` user (name is randomized)
* Secure by default — `builder` (temporary admin) has no login access and is removed after use
* Systemd-based background service
* Per-user autostart for GUI
* Unobtrusive during normal use

---

### Why It’s Great for Casual Linux Users

Many users forget to update or aren’t comfortable using the terminal.
**user_updater** solves that by:

* Updating the system in the background — no terminal needed (after install)
* Showing a popup while updates run, so you know not to reboot
* Keeping your system safe with zero input

Safe by design:

* Runs updates as a temporary system user
* Doesn’t require repeated password prompts (only on first install)
* Deletes the `builder` (temporary admin) user after each update

---

### Install

**Simple install (requires sudo):**

```bash
curl -s https://codeberg.org/marvin1099/user_updater/raw/branch/main/install.sh | sudo bash
```

Don’t have sudo yet?  
Use this instead (requires root password first):

```bash
curl -fsS https://codeberg.org/marvin1099/user_updater/raw/branch/main/get_dependencies.sh -o /tmp/get_dependencies.sh && chmod +x /tmp/get_dependencies.sh && su -c "/tmp/get_dependencies.sh" && curl -fsS https://codeberg.org/marvin1099/user_updater/raw/branch/main/install.sh | sudo bash; rm -f /tmp/get_dependencies.sh
```

**Install with options:**

```bash
curl -s https://codeberg.org/marvin1099/user_updater/raw/branch/main/install.sh false "" true | sudo bash
```

* `false`: disables self-updates (default is true)  
  When enabled it will updates updater scripts.
* `""`: leaves forced self-update setting unchanged (default is true)  
  When enabled it will force script updates.
* `true`: enables service file reactivation (default is true)  
  When enabled will enable the systemd service file on manual update.

---

### Trust & Transparency

**user_updater** is licensed under the **AGPLv3** — it’s free, open source, and fully auditable.

* No tracking
* No hidden behavior
* No vendor lock-in
* Just read the scripts — everything is in plain Bash

---

### Dependencies

Installed automatically if possible:

```
git awk sudo topgrade yad systemd
```

Fallback: install them using your distro’s package manager (`pacman`, `apt`, `dnf`, etc.)

Common required Unix tools (typically preinstalled):

```
cd mkdir dirname touch cat id rm kill who sleep read ps readlink mktemp basename useradd chown chmod passwd getent usermod tail head grep cut ls date stat yes tee
```

---

### How It Works

1. **Systemd initiates the update script:**  
   A service runs the update script in the background.

2. **Creates a temp user:**  
   A randomized `builder` user with passwordless sudo access, runs the updates over `topgrade`.

3. **GUI notifies users:**  
   A popup appears for each logged-in desktop user.

4. **Cleans up afterward:**  
   The temporary user is deleted to remove access and risk.

---

### After Installation

* **Restart or log out/in** to enable background and GUI updates.
* Adding new users? Restart once (to register them), then again (or log out/in) for GUI activation.

To register users manually:

```bash
sudo /var/lib/user_updater/register_updater_gui.sh
```

To launch the GUI manually (as the target user / new user):

```bash
/home/$USER/.config/user_updater/gui_report.sh & disown
```

To reregister and start the gui on the running user (new user), you can also use Reinstall (see bellow).

---

### File Locations & Maintenance

* App directory:
  `/var/lib/user_updater`

* Log files:
  `/var/lib/user_updater/logs/`

* Reinstall by running (The options listed at the end of the Install section can be used here as well):

  ```bash
  sudo /var/lib/user_updater/install.sh
  ```

* Manually trigger update:

  ```bash
  sudo systemctl start user_updater
  ```

* Uninstall completely:

  ```bash
  sudo /var/lib/user_updater/uninstall.sh
  ```
