#!/usr/bin/env bash

# Ensure the script is run as root
if [ "$EUID" -ne 0 ]; then
    echo "This script must be run as root. Exiting."
    exit 1
fi

SCRIPT=$(readlink -f "$0")
SCRIPTPATH=$(dirname "$SCRIPT")

cd "$SCRIPTPATH" || exit 1

loginfo=$(./main_logger.sh "" "Cleanup" "Update" "update")
UUPDATER_IDATE=$(echo "$loginfo" | sed -n '1p')
export UUPDATER_IDATE
UUPDATER_ACTION=$(echo "$loginfo" | sed -n '2p')
export UUPDATER_ACTION
admin_log=$(echo "$loginfo" | sed -n '3p')
log() {
    echo "$1" | tee -a "$admin_log"
}
echo "$loginfo" | sed -n '4,$p'

./delete_and_note_users.sh

log "Cleaning report file"
rm -f /tmp/topgrade-report.log

log "Killing Service if running"
setsid bash -c "sleep 1; systemctl is-active user_updater.service | awk '/activ/ && !/inactiv/' | grep -q . && systemctl kill user_updater.service 2>/dev/null" &