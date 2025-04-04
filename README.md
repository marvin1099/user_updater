### **user_updater**

A simple tool for automatic system updates using [`topgrade`](https://github.com/topgrade-rs/topgrade),  
featuring a background systemd service and a lightweight GUI to notify users of ongoing updates.

> Only Unix systems are supported.  
> macOS *might* work, but is not officially supported.

---

###  Features

- Automatic system updates via [`topgrade`](https://github.com/topgrade-rs/topgrade)
- GUI notification when updates are in progress (`yad` popup window)
- A temporary, passwordless `builder` user (name randomized)
- Secure by default — `builder` has no login access and is deleted after use
- Background updates via systemd
- GUI notification autostarts per user
- Minimal system impact — zero interference with your daily use

---

### Why It's Great for New or Non-Technical Linux Users

Many Linux users especially beginners or those who use Linux casually  
forget or don’t know how to keep their systems updated.

**user_updater** solves this by:

- Automatically updating your system in the background — no terminal skills required
- Showing a friendly popup when updates are happening, so you know not to shut down or reboot during critical updates
- Ensuring your system stays secure and maintained with minimal input

It also runs safely by:
- Using a separate background user
- Not requiring you to enter your password every time
- Deleting the `builder` user after its job is done, preventing abuse

---

### Install

Install with a single command:

```bash
bash <(curl -s https://codeberg.org/marvin1099/user_updater/raw/branch/main/install.sh)
```

---

### Dependencies

You'll need the following tools installed (the installer will try to fetch them for you):

```
git awk sudo topgrade yad
```

If any are missing, install them manually using your package manager (e.g., `pacman`, `apt`, `dnf`, etc.), and ensure they’re in your `PATH`.

---

### How It Works

1. **Creates a temporary user:**  
   A locked, randomized `builder` user is created with passwordless `sudo` access — only used for running updates.

2. **Systemd handles updates:**  
   A service runs at boot as root, switches to `builder`, and launches `topgrade` to update your system silently in the background.

3. **Notifies logged-in users:**  
   A lightweight GUI (`yad`) pops up on each desktop user account, showing that updates are in progress.

4. **Self-cleans:**  
   Once the update job is complete, the `builder` user is deleted to eliminate any potential security risk.

---

### After Installation

- **Restart or relogin** to activate the updater and GUI.
- **Adding new users?**  
  Restart once to index them, and again to show the GUI for them.

Even if the GUI doesn’t show up yet, updates are still running in the background.


### File Locations & Maintenance

- App files live in:  
  `/var/lib/user_updater`

- To update the updater itself:  
  ```bash
  /var/lib/user_updater/install.sh
  ```

- To uninstall completely:  
  ```bash
  /var/lib/user_updater/uninstall.sh
  ```
