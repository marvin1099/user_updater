#!/bin/bash

# Ensure the script is run as root
if [ "$EUID" -ne 0 ]; then
    echo "This script must be run as root. Exiting."
    exit 1
fi

SCRIPT=$(readlink -f $0)
SCRIPTPATH=$(dirname "$SCRIPT")

cd "$SCRIPTPATH"

loginfo=$(./main_logger.sh "" "GUI Updater register" "Install" "install")
admin_log="$(echo "$loginfo" | head -1)"
log() {
    echo "$1" | tee -a "$admin_log"
}
echo "$(echo "$loginfo" | tail -n +2)"

# Path to the script you want to register for autostart
script_path="gui_report.sh"
tool_updater="update_user_tools.sh"
logger="main_logger.sh"

log "Checking users in /home"
# Loop through all users on the system
for user in $(ls /home); do
    # Skip system users
    if [[ ! -d "/home/$user" ]] || ! id "$user" &>/dev/null; then
        continue
    fi

    log "Found user \"$user\""
    log "Creating autostart and config directory if missing"
    # Create the autostart directory if it doesn't exist
    sudo -u "$user" mkdir -p "/home/$user/.config/autostart"
    sudo -u "$user" mkdir -p "/home/$user/.config/user_updater"

    log "Defining file locations"
    # Create the .desktop file for the autostart entry
    desktop_file="/home/$user/.config/autostart/gui_report.desktop"
    new_scipt_path="/home/$user/.config/user_updater/gui_report.sh"
    new_tool_updater="/home/$user/.config/user_updater/update_user_tools.sh"
    new_logger="/home/$user/.config/user_updater/main_logger.sh"

    log "Copying user scripts to user directory and setting correct permissions"
    cat "$script_path" > "$new_scipt_path"
    chmod u+rwx "$new_scipt_path"
    chown "$user":"$user" "$new_scipt_path"

    cat "$tool_updater" > "$new_tool_updater"
    chmod u+rwx "$new_tool_updater"
    chown "$user":"$user" "$new_tool_updater"

    cat "$logger" > "$new_logger"
    chmod u+rwx "$new_logger"
    chown "$user":"$user" "$new_logger"

    log "Creating the autostart file"
    # Ensure the desktop file is not already present
    if [ -f "$desktop_file" ]; then
        log "Autostart entry already exists for user $user. Skipping..."
        continue
    fi

    # Write the .desktop entry for autostart
    echo "[Desktop Entry]" > "$desktop_file"
    echo "Type=Application" >> "$desktop_file"
    echo "Exec=bash '$new_scipt_path'" >> "$desktop_file"
    echo "Name=Update GUI Report" >> "$desktop_file"
    echo "Comment=Start the Update GUI report automatically" >> "$desktop_file"
    echo "X-GNOME-Autostart-enabled=true" >> "$desktop_file"

    log "Make shure autostart file can be run and set the the current user as owner"
    # Make sure the script is executable
    chmod u+rwx "$desktop_file"

    # Set the correct ownership for the created .desktop file (user-specific)
    chown "$user":"$user" "$desktop_file"

    log "Autostart entry added for user $user"
done

log "Autostart registration complete"
log ""
