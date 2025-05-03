#!/bin/bash

# Ensure the script is run as root
if [ "$EUID" -ne 0 ]; then
    echo "This script must be run as root. Exiting."
    exit 1
fi

SCRIPT=$(readlink -f $0)
SCRIPTPATH=$(dirname "$SCRIPT")

cd "$SCRIPTPATH"

loginfo=$(./main_logger.sh "" "Systemd register" "Install" "install")
admin_log="$(echo "$loginfo" | head -1)"
log() {
    echo "$1" | tee -a "$admin_log"
}
echo "$(echo "$loginfo" | tail -n +2)"

SERVICE_FILE="/etc/systemd/system/user_updater.service"
log "Checking if the service file \"$SERVICE_FILE\" exists"
if [[ -f "$SERVICE_FILE" ]]; then
    status=1
fi

log "Recreating the service file"
# Create systemd service file
cat > "$SERVICE_FILE" <<EOF
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

log "Reloading systemd services list"
# Reload systemd to recognize the new service
systemctl daemon-reload

# Only enable if it was not there previously
if [[ -z "$status" ]]; then 
    log "Starting the service because the file was missing"
    # Enable the service to start on boot
    systemctl enable user_updater.service
else
    log "Skipping starting the service because the file already existed"
fi
log "Service user_updater was registerd"
log ""

