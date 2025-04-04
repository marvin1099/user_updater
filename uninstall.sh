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

rm -r "$install_dir"
