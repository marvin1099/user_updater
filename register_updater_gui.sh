#!/bin/bash

# Ensure the script is run as root
if [ "$EUID" -ne 0 ]; then
    echo "This script must be run as root. Exiting."
    exit 1
fi

SCRIPT=$(readlink -f "$0")
SCRIPTPATH=$(dirname "$SCRIPT")

cd "$SCRIPTPATH"

# Path to the script you want to register for autostart
script_path="gui-report.sh"

# Check if the script exists
if [ ! -f "$script_path" ]; then
    echo "Error: $script_path does not exist. Please provide the correct path."
    exit 1
fi

# Loop through all users on the system
for user in $(ls /home); do
    # Skip system users
    if [[ ! -d "/home/$user" ]] || ! id "$user" &>/dev/null; then
        continue
    fi

    # Create the autostart directory if it doesn't exist
    sudo -u "$user" mkdir -p "/home/$user/.config/autostart"
    sudo -u "$user" mkdir -p "/home/$user/.config/user_updater"

    # Create the .desktop file for the autostart entry
    desktop_file="/home/$user/.config/autostart/gui-report.desktop"
    new_scipt_path="/home/$user/.config/user_updater/gui-report.sh"

    cat "$script_path" > "$new_scipt_path"
    chmod u+rwx "$new_scipt_path"
    chown "$user":"$user" "$new_scipt_path"

    # Ensure the desktop file is not already present
    if [ -f "$desktop_file" ]; then
        echo "Autostart entry already exists for user $user. Skipping..."
        continue
    fi

    # Write the .desktop entry for autostart
    echo "[Desktop Entry]" > "$desktop_file"
    echo "Type=Application" >> "$desktop_file"
    echo "Exec=bash '$new_scipt_path'" >> "$desktop_file"
    echo "Name=Update GUI Report" >> "$desktop_file"
    echo "Comment=Start the Update GUI report automatically" >> "$desktop_file"
    echo "X-GNOME-Autostart-enabled=true" >> "$desktop_file"

    # Make sure the script is executable
    chmod u+rwx "$desktop_file"

    # Set the correct ownership for the created .desktop file (user-specific)
    chown "$user":"$user" "$desktop_file"

    echo "Autostart entry added for user $user."
done

echo "Autostart registration complete."
