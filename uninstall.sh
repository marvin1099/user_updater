#!/bin/bash

# Ensure the script is run as root
if [ "$EUID" -ne 0 ]; then
    echo "This script must be run as root. Exiting."
    exit 1
fi

install_dir="/var/lib/user_updater"

SERVICE_FILE="/etc/systemd/system/user_updater.service"
systemctl stop user_updater.service
rm -f "$SERVICE_FILE"

./delete_and_note_users.sh

for user in $(ls /home); do
    # Skip system users
    if [[ ! -d "/home/$user" ]] || ! id "$user" &>/dev/null; then
        continue
    fi
    g_pid=$(ps aux | awk '/gui_report.sh/ && !/awk/ {if ($1 == "'$user'" && $11 ~ "bash" && $12 ~ "user_updater/gui_report.sh") print $2}' | head -n 1)
    if [[ -n $g_pid ]]; then
        kill -9 $g_pid
    fi
    desktop_file="/home/$user/.config/autostart/gui_report.desktop"
    new_scipt_path="/home/$user/.config/user_updater/gui_report.sh"
    rm -f $desktop_file
    rm -f $new_scipt_path
done

rm -rf "$install_dir"
