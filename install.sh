#!/usr/bin/env bash

# Ensure the script is run as root
if [ "$EUID" -ne 0 ]; then
    echo "This script must be run as root. Exiting."
    exit 1
fi

# Admin log setup
log_dir="/var/lib/user_updater/logs"
mkdir -p "$log_dir"
chmod a+wr "$log_dir"
if [[ -z "$UUPDATER_IDATE" ]]; then
    UUPDATER_IDATE="$(date '+%F_%H-%M-%S')"
    export UUPDATER_IDATE
fi
if [[ -z "$UUPDATER_ACTION" ]]; then
    UUPDATER_ACTION="install"
    export UUPDATER_ACTION
fi
admin_log="$log_dir/${UUPDATER_IDATE}_$UUPDATER_ACTION.log"
log() {
    echo "$1" | tee -a "$admin_log"
}
if [[ ! -f "$admin_log" ]]; then
    touch "$admin_log"
    chmod 664 "$admin_log"
    echo "Logs are saved to \"$log_dir\"."
    log "Starting Install log at \"$UUPDATER_IDATE\"."
else
    log ""
fi
log "Starting Main Install script."

install_dir="/var/lib/user_updater"
log "Checking if run from install directory \"$install_dir\" and if dependencies script is present..."
if [[ -f "get_dependencies.sh" ]] && [[ "$(pwd)" != "$install_dir" ]]; then
    log "Found install directory and dependencies script."
    ./get_dependencies.sh && deps=1
else
    log "Install directory not present or dependencies install script not found."
fi

log "Checking if the repo was cloned to the install directory \"$install_dir\"."
git=0
if cd "$install_dir"; then
    if git rev-parse --is-inside-work-tree 2> /dev/null
    then
        log "Found git repo. Trying to pull git update..."
        git pull 2> /dev/null # 2>&1
        git=1
    else
        log "No git repo found in install directory."
    fi
else
    log "Install directory not present."
fi

log "Make shure parrent of install directory exists."
mkdir -p "$(dirname "$install_dir")"

log "Navigating to parrent of install directory."
cd "$(dirname "$install_dir")" || exit 1

if [[ $git -eq 0 ]]; then
    log "Trying to Clone user_updater repository..."
    if ! git clone https://codeberg.org/marvin1099/user_updater
    then
        log "Primary repo clone failed, trying Backup repo..."
        if ! git clone https://github.com/marvin1099/user_updater
        then
            log "Error downloading the repo from both sources. Exiting..."
            sleep 1
            exit 1
        fi
    fi
    log "Success cloning user_updater repository."
fi

log "Navigating to install directory."
cd "$install_dir" || exit 1

if [[ -z "$deps" ]]; then
    ./get_dependencies.sh || exit 1
fi

./register_systemd.sh

./register_updater_gui.sh

./self_update.sh "true" "$1" "$2" "$3"

if [[ -n "$SUDO_USER" ]]; then
    log "Testing if GUI output is available for user $SUDO_USER..."
    report_gui="/home/$SUDO_USER/.config/user_updater/gui_report.sh"
    yad --text="Testing if Gui output is available." --no-buttons --timeout=1 --no-focus --undecorated --posx 0 --posy 0 --width=350 --height=40
    rep=$?
    log "The test GUI reported an exit code of \"$rep\"."
    if [[ $rep -eq 70 && -f "$report_gui" ]]; then
        log "GUI test successful. Preparing to start updates..."
        g_pid=$(ps aux | awk '/gui_report.sh/ && !/awk/ {if ($1 == "'"$SUDO_USER"'" && $11 ~ "bash" && $12 ~ "user_updater/gui_report.sh") print $2}' | head -n 1) #'
        if [[ -n $g_pid ]]; then
            log "Killing existing gui_report.sh process (PID: $g_pid)..."
            kill -9 "$g_pid"
        fi
        log "Launching new gui_report.sh process for $SUDO_USER..."
        sudo -u "$SUDO_USER" setsid env UUPDATER_IDATE="$UUPDATER_IDATE" UUPDATER_ACTION="$UUPDATER_ACTION" "$report_gui" >/dev/null 2>&1 &
        log "Starting user_updater systemd service in the backround..."
        setsid systemctl start user_updater.service &
    else
        log "GUI report not started, either GUI not available or gui_report.sh missing."
    fi
else
    log "No SUDO_USER found, skipping GUI integration."
fi

log "Installation process complete."
log ""
