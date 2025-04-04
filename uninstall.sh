#!/bin/bash

# Ensure the script is run as root
if [ "$EUID" -ne 0 ]; then
    echo "This script must be run as root. Exiting."
    exit 1
fi

install_dir="/var/lib/user_updater"

SERVICE_FILE="/etc/systemd/system/user_updater.service"
systemctl stop user_updater.service
rm "$SERVICE_FILE"

./delete_and_note_users.sh

for user in $(ls /home); do
    desktop_file="/home/$user/.config/autostart/gui-report.desktop"
    new_scipt_path="/home/$user/.config/user_updater/gui_report.sh"
    rm $desktop_file
    rm $new_scipt_path
done

rm -r "$install_dir"
