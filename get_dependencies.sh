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
log "Starting Dependencies install script."

log "Setting Dependencies Dictionary."
# Map of packages and the commands they provide
declare -A deps=(
    [git]="git"
    [awk]="awk"
    [sudo]="sudo"
    [topgrade]="topgrade"
    [yad]="yad"
    [systemd]="systemctl"
)

log "Detecting package manager."
# Detect package manager
if command -v pacman &>/dev/null; then
    PKG_MANAGER="pacman -S --noconfirm"
elif command -v apt &>/dev/null; then
    PKG_MANAGER="apt install -y"
elif command -v dnf &>/dev/null; then
    PKG_MANAGER="dnf install -y"
elif command -v zypper &>/dev/null; then
    PKG_MANAGER="zypper install -y"
elif command -v brew &>/dev/null; then
    PKG_MANAGER="brew install"
elif command -v apk &>/dev/null; then
    PKG_MANAGER="apk add"
elif command -v emerge &>/dev/null; then
    PKG_MANAGER="emerge"
elif command -v xbps-install &>/dev/null; then
    PKG_MANAGER="xbps-install -y"
elif command -v pkg &>/dev/null; then
    PKG_MANAGER="pkg install -y"
else
    PKG_MANAGER=""
fi
log "Using package manager command \"$PKG_MANAGER\"."

log "Checking for installed dependencies."
# Collect missing dependencies
to_install=()
for pkg in "${!deps[@]}"; do
    cmd="${deps[$pkg]}"
    if command -v "$cmd" &>/dev/null; then
        log "$pkg ($cmd) is already installed. Skipping..."
    else
        log "Adding \"$pkg\" to dictionary of missing dependencies."
        to_install+=("$pkg")
    fi
done

# Install missing dependencies if any
if [ ${#to_install[@]} -gt 0 ]; then
    if [[ -z "$PKG_MANAGER" ]]; then
        log "Unsupported package manager. Please install the following packages manually:"
        for pkg in "${!deps[@]}"; do
            cmd="${deps[$pkg]}"
            printf "\"$pkg\" " | tee -a "$admin_log"
        done
        log ""
        exit 1
    fi
    log "Installing missing packages: ${to_install[*]}"
    $PKG_MANAGER "${to_install[@]}"

    # Verify installation
    err=0
    for pkg in "${!deps[@]}"; do
        cmd="${deps[$pkg]}"
        if ! command -v "$cmd" &>/dev/null; then
            if [[ "$err" -eq 0 ]]; then
                log "Error installing the following packages:"
            fi
            err=1
            printf "\"$pkg\" " | tee -a "$admin_log"
        fi
    done
    if [[ "$err" -eq 1 ]]; then
        log ""
    fi
    for pkg in "${!deps[@]}"; do
        cmd="${deps[$pkg]}"
        if ! command -v "$cmd" &>/dev/null; then
            log "The expected command to be available for \"$pkg\" was \"$cmd\"."
        fi
    done

    if [[ "$err" -eq 1 ]]; then

        log "Install these manually."
        log "Can't continue without dependencies."
        log "Exiting..."
        exit 1
    fi
else
    log "No need to install packages, all packages where detected."
fi

log "All dependencies installed."
