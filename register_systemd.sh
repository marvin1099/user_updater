#!/usr/bin/env bash

# Ensure the script is run as root
if [ "$EUID" -ne 0 ]; then
    echo "This script must be run as root. Exiting."
    exit 1
fi

SCRIPT=$(readlink -f "$0")
SCRIPTPATH=$(dirname "$SCRIPT")

cd "$SCRIPTPATH" || exit 1

loginfo=$(./main_logger.sh "" "Systemd register" "Install" "install")
UUPDATER_IDATE=$(echo "$loginfo" | sed -n '1p')
export UUPDATER_IDATE
UUPDATER_ACTION=$(echo "$loginfo" | sed -n '2p')
export UUPDATER_ACTION
admin_log=$(echo "$loginfo" | sed -n '3p')
log() {
    echo "$1" | tee -a "$admin_log"
}
echo "$loginfo" | sed -n '4,$p'

SERVICE_FILE="/etc/systemd/system/user_updater.service"
TIMER_FILE="/etc/systemd/system/user_updater.timer"

log "Registering User Updater Service and Timer"

if [[ -f "$SERVICE_FILE" ]]; then
    log "Service file already exists: $SERVICE_FILE"
    svc_exists=1
fi

if [[ -f "$TIMER_FILE" ]]; then
    log "Timer file already exists: $TIMER_FILE"
    timer_exists=1
fi

# Create or overwrite service file
log "Writing service file."
cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=User Updater Service
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/var/lib/user_updater/user_updater.sh
ExecStop=/var/lib/user_updater/cleanup.sh
User=root
TimeoutStartSec=infinity
TimeoutStopSec=180
WorkingDirectory=/var/lib/user_updater
KillMode=control-group
EOF

# Create or overwrite timer file
log "Writing timer file."
cat > "$TIMER_FILE" <<EOF
[Unit]
Description=Weekly timer for User Updater
After=network-online.target
Wants=network-online.target

[Timer]
OnCalendar=Thu 03:00
OnCalendar=Sun 03:00
RandomizedDelaySec=60
Persistent=true

[Install]
WantedBy=timers.target
EOF

log "Reloading systemd units."
systemctl daemon-reload

systemctl disable user_updater.service 2>/dev/null
if [[ -z "$svc_exists" ]] || [[ -z "$timer_exists" ]]; then
    log "Timer or Service was missing, re-enabling timer."
    systemctl enable --now user_updater.timer
else
    log "Timer and Service already existed, ensuring timer runns."
    systemctl restart user_updater.timer
fi

log "Systemd registration complete: Service & Timer registered."

