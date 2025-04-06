## **user_updater**

A simple tool for automatic system updates using [`topgrade`](https://github.com/topgrade-rs/topgrade),  
featuring a background systemd service and a lightweight GUI to notify users of ongoing updates.

> Only Unix systems are supported.  
> macOS *might* work, but is not officially supported.

---

### Features

- Automatic system updates via [`topgrade`](https://github.com/topgrade-rs/topgrade)
- GUI notification when updates are in progress (`yad` popup window)
- A temporary, passwordless `builder` user (name randomized)
- Secure by default — `builder` has no login access and is deleted after use
- Background updates via systemd
- GUI notification autostarts per user
- Minimal system impact — zero interference with your daily use

---

### Why It's Great for New or Casual Linux Users

Many Linux users — especially beginners or those who use Linux casually —  
either forget or don’t know how to keep their systems updated.

**user_updater** fixes that by:

- Automatically updating your system in the background — no terminal skills required  
- Showing a popup while updates are happening, so you know not to shut down or reboot during critical processes  
- Keeping your system secure and maintained with minimal input

It also runs safely by:
- Using a separate background user
- Not requiring you to enter your password every time
- Deleting the `builder` user after its job is done, reducing risk of misuse

---

### Install

Install with a single command:

```bash
curl -s https://codeberg.org/marvin1099/user_updater/raw/branch/main/install.sh | sudo bash
```

Your user password will be requested during installation.

If you **don’t have sudo yet**, use this command instead — it also installs sudo:

```bash
cd /tmp && curl -fsS https://codeberg.org/marvin1099/user_updater/raw/branch/main/get_dependencies.sh -o get_dependencies.sh && chmod +x get_dependencies.sh && su -c "./get_dependencies.sh" && curl -fsS https://codeberg.org/marvin1099/user_updater/raw/branch/main/install.sh | sudo bash; rm -f get_dependencies.sh
```

Here, the **root password** will be required instead.

---

### Trust & Transparency

You don’t have to trust me — or even this code.

As mentioned in the [LICENSE](./LICENSE) file, **user_updater** is licensed under the **AGPLv3**, which means it's fully **open source** and **libre software**.

That means:
- You can read exactly what the code is doing  
- You can modify or fork your own version  
- You can share it with others — as long as the same freedom is preserved

There’s no tracking, no hidden behavior, no proprietary lock-in.  
If you’re ever unsure, just **read the scripts before running them** — it’s all right there.

---

### Dependencies

The following tools are required (the installer will attempt to install them automatically):

```
git awk sudo topgrade yad systemd
```

If any can't be installed by the script, use your package manager (`pacman`, `apt`, `dnf`, etc.)  
to install them manually, and ensure they're in your `PATH`.

Standard Unix tools are also expected to be available. Most systems already include these,  
but for reference, here’s the complete list:

```
cd mkdir dirname touch cat id rm kill who sleep read ps readlink mktemp basename useradd chown chmod passwd getent usermod tail head grep cut ls date stat yes
```

---

### How It Works

1. **Creates a temporary user:**  
   A locked, randomized `builder` user is created with passwordless `sudo` access — used only for running updates.

2. **Systemd runs updates:**  
   A service launches at boot as root, switches to `builder`, and silently runs `topgrade` to perform the update.

3. **Notifies desktop users:**  
   A simple GUI (`yad`) pops up for each logged-in desktop user, showing that updates are in progress.

4. **Self-cleans:**  
   Once done, the `builder` user is removed to eliminate any long-term access or risk.

---

### After Installation

- **Restart or log out and back in** to activate the updater and GUI.
- **Adding new users?**  
  Restart once to index them, and again to show the GUI on their account.

Even if the GUI hasn’t shown up yet, updates are still running in the background.

To register new users immediately, run:
```bash
sudo /var/lib/user_updater/register_updater_gui.sh
```

To skip the second re-login step, run this **as the GUI user**:
```bash
/home/$USER/.config/user_updater/gui_report.sh & disown
```

Running both of these will fully skip the re-login process.  
See below for how to manually trigger an update.

Alternately run rerun `./install.sh` (see below).

---

### File Locations & Maintenance

- App files are located in:  
  `/var/lib/user_updater`

- To update or reinstall the updater:  
  ```bash
  sudo /var/lib/user_updater/install.sh
  ```

- To uninstall completely:  
  ```bash
  sudo /var/lib/user_updater/uninstall.sh
  ```

- To trigger an update manually:  
  ```bash
  sudo systemctl start user_updater
  ```