### **user_updater**
A simple tool for automatic system updates using `topgrade`, featuring a background process and a simple GUI to show progress.

Only Unix systems are suported.  
A Mac system may work but no promises. 

---

### Features
- Automatic system updates using [topgrade](https://github.com/topgrade-rs/topgrade)
- GUI notification when updates are running
- Dedicated `builder` user for background tasks (name randomized)
- Secure: no password login for builder, runs updates via systemd
- Runs automatically in the background after install
- Works system-wide for all users (autostarts GUI per user; yad window)

---

### Install
You can install `user_updater` with a single command:

```bash
bash <(curl -s https://codeberg.org/marvin1099/user_updater/raw/branch/main/install.sh)
```

---

# Dependencies

The app need standard unix tools.  
It will also need `git awk sudo topgrade yad`.  
These should get pulled by the installer.  
if they do not get pulled install them manually,  
and make shure they are added to yor PATH varrible. 

---

### How It Works
   
1. **Creates a special user:**  
   The installer sets up a locked `builder` user with passwordless sudo access.
      
2. **Sets up a systemd service:**  
   A root-level systemd service launches on boot, switches to `builder`, and runs `topgrade` silently in the background.
   
3. **Registers a GUI autostart app:**  
   Each user gets a desktop GUI app that notifies when updates are being run.

Keep in mind that the `builder` user gets deleted after use as to prevent any atacks.  
After all this `builder` user can run sudo without a password.  
This could potentially lead to passwordless root access for anyone.  
This is why this user gets removed after use.
   
---

### After Installation
- **Restart or relogin is required** to activate everything properly.
- **Adding new users?** Restart once to index them, and again for the GUI to show.

Updates will still run in the background **even if the GUI isn't showing yet.**  
On First time install one Restart or relogin is required to start the updates.

---

# About the install

After running the installer the scripts will live in `/var/lib/user_updater`.  
Running `/var/lib/user_updater/install.sh` after the app was installed will update the app.  
Running `/var/lib/user_updater/uninstall.sh` will remove the app when it is installed.
