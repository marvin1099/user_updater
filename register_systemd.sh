#!/bin/bash

# Ensure the script is run as root
if [ "$EUID" -ne 0 ]; then
    echo "This script must be run as root. Exiting."
    exit 1
fi

SERVICE_FILE="/etc/systemd/system/user_updater.service"

# Create systemd service file
sudo bash -c "cat > '$SERVICE_FILE'" <<EOF
[Unit]
Description=User Updater Service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=bash /var/lib/user_updater/user_updater.sh
Restart=on-failure
User=root
WorkingDirectory=/var/lib/user_updater

[Install]
WantedBy=multi-user.target
EOF

# Reload systemd to recognize the new service
systemctl daemon-reload

# Disable the service if enabled
systemctl disable user_updater.service 2>/dev/null

# Enable the service to start on boot
systemctl enable user_updater.service

# Start the service immediately
#systemctl start user_updater.service

echo "Service user_updater has been created"

