#!/usr/bin/env bash

# Ensure the script is run as root
if [ "$EUID" -ne 0 ]; then
    echo "This script must be run as root. Exiting."
    exit 1
fi

SCRIPT=$(readlink -f "$0")
SCRIPTPATH=$(dirname "$SCRIPT")

cd "$SCRIPTPATH" || exit 1

loginfo=$(./main_logger.sh "/tmp/user_updater_uninstaller" "Uninstaller" "Uninstall" "uninstall" "*")
UUPDATER_IDATE=$(echo "$loginfo" | sed -n '1p')
export UUPDATER_IDATE
UUPDATER_ACTION=$(echo "$loginfo" | sed -n '2p')
export UUPDATER_ACTION
admin_log=$(echo "$loginfo" | sed -n '3p')
log() {
    echo "$1" | tee -a "$admin_log"
}
echo "$loginfo" | sed -n '4,$p'

log "Setting install directory."
install_dir="/var/lib/user_updater"

log "Setting service file location."
SERVICE_FILE="/etc/systemd/system/user_updater.service"
TIMER_FILE="/etc/systemd/system/user_updater.timer"

log "Stopping services."
systemctl stop user_updater.service
systemctl disable --now user_updater.timer

log "Removing service files."
rm -f "$SERVICE_FILE"
rm -f "$TIMER_FILE"

./delete_and_note_users.sh

log "Removing updater in computer users."
for dir in /home/*; do
    # Skip system users
    user="$(basename "$dir")"
    if [[ ! -d "$dir" ]] || ! id "$user" &>/dev/null; then
        continue
    fi
    log "Found user \"$user\", killing gui report script."
    g_pid=$(ps aux | awk '/gui_report.sh/ && !/awk/ {if ($1 == "'"$user"'" && $11 ~ "bash" && $12 ~ "user_updater/gui_report.sh") print $2}' | head -n 1)
    if [[ -n $g_pid ]]; then
        kill -9 "$g_pid"
    fi
    log "Deleting user autostart entry and updater in user config folder."
    updater_config_dir="$dir/.config/user_updater/"
    desktop_file="$dir/.config/autostart/gui_report.desktop"
    rm -f "$desktop_file"
    rm -rf "$updater_config_dir"
done

log "Finished deleting remains of updater in users."

log "Copying uninstaller log to /tmp and deleting main updater install directory."
log ""
cat "$admin_log" >> /tmp/user_updater_uninstaller.log

rm -rf "$install_dir"
