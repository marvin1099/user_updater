#!/bin/bash

SCRIPT=$(readlink -f "$0")
SCRIPTPATH=$(dirname "$SCRIPT")

cd "$SCRIPTPATH" || exit 1

if [[ -z "$UUPDATER_IDATE" ]]; then
    UUPDATER_IDATE="$(date '+%F_%H-%M-%S')"
    export UUPDATER_IDATE
fi
suffix="${USER}updatereturn"
UUPDATER_ACTION="$suffix"
export UUPDATER_ACTION

log_dir="/var/lib/user_updater/logs"
log_suffix="_$suffix.log"

# Try to find a recent log within the last minute
recent_log=$(find "$log_dir" -maxdepth 1 -type f -name "*$log_suffix" -mmin -1 -print0 | sort -z | tail -zn1 | xargs -0)

# Set log to use to recent_log if set otherwise generate log filename
if [[ -n "$recent_log" ]]; then
    PERM_LOG="$recent_log"
else
    PERM_LOG="$log_dir/${UUPDATER_IDATE}${log_suffix}"
fi

log() {
    echo "$1" | tee -a "$PERM_LOG"
}
if [[ -f "$PERM_LOG" ]]; then
    echo "Logs are saved to \"$log_dir\""
    log "Starting User Updater log at $UUPDATER_IDATE"
else
    log ""
fi
log "Starting User Tool Updater script"

log "Updating user-level tools..."

# Update Flatpak apps (user scope only)
if command -v flatpak >/dev/null; then
    log "Updating Flatpak (user)..."
    flatpak update --user -y
fi

# Update pipx packages
if command -v pipx >/dev/null; then
    log "Upgrading pipx packages..."
    pipx upgrade-all
fi

# Update user-installed pip packages
if command -v pip >/dev/null; then
    log "Upgrading pip user packages..."
    if command -v jq >/dev/null; then
        python3 -m pip list --user --outdated --format=json | jq -r '.[].name' | while read -r pkg; do
            python3 -m pip install --user --upgrade "$pkg"
        done
    else
        # Fallback: parse the human-readable format
        python3 -m pip list --user --outdated | awk 'NR>2 {print $1}' | while read -r pkg; do
            python3 -m pip install --user --upgrade "$pkg"
        done
    fi
fi

# Update user-installed npm global packages
if command -v npm >/dev/null && npm config get prefix | grep -q "$HOME"; then
    log "Upgrading npm user packages..."
    npm update -g
fi

# Update cargo-installed binaries (Rust)
if command -v cargo >/dev/null; then
    log "Upgrading cargo-installed tools..."
    if cargo install-update -V >/dev/null 2>&1; then
        cargo install-update -a
    else
        log "To enable cargo updates run the command:"
        log "cargo install cargo-update"
    fi
fi

# Update Ruby gems installed in user home
if command -v gem >/dev/null; then
    log "Upgrading Ruby gems (user)..."
    gem update --user-install
fi

log "Done updating user-level tools!"
log ""
