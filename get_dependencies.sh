#!/bin/bash

# Ensure the script is run as root
if [ "$EUID" -ne 0 ]; then
    echo "This script must be run as root. Exiting."
   cd "$SCRIPTPATH" || exit 1
fi

# Admin log setup
log_dir="/var/lib/user_updater/logs"
mkdir -p "$log_dir"
chmod a+wr "$log_dir"
if [[ -z "$UUPDATER_IDATE" ]]; then
    UUPDATER_IDATE="$(date '+%F_%H-%M-%S')"
    export UUPDATER_IDATE
    uuset=1
fi
if [[ -z "$UUPDATER_ACTION" ]]; then
    export UUPDATER_ACTION="install"
fi
admin_log="$log_dir/${UUPDATER_IDATE}_$UUPDATER_ACTION.log"
touch "$admin_log"
chmod 664 "$admin_log"
log() {
    echo "$1" | tee -a "$admin_log"
}
if [[ -z "$uuset" ]]; then
    echo "Logs are saved to \"$log_dir\""
    log "Starting Install log at $UUPDATER_IDATE"
else
    log ""
fi
log "Starting Dependencies install script"

log "Setting Dependencies Dictionary"
# Map of packages and the commands they provide
declare -A deps=(
    [git]="git"
    [awk]="awk"
    [sudo]="sudo"
    [topgrade]="topgrade"
    [yad]="yad"
    [systemd]="systemctl"
)

log "Detecting package manager"
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
    log "Unsupported package manager. Please install manually."
   cd "$SCRIPTPATH" || exit 1
fi
log "Using package manager command \"$PKG_MANAGER\""

log "Checking for installed dependencies"
# Collect missing dependencies
to_install=()
for pkg in "${!deps[@]}"; do
    cmd="${deps[$pkg]}"
    if command -v "$cmd" &>/dev/null; then
        log "$pkg ($cmd) is already installed. Skipping..."
    else
        log "Adding \"$pkg\" to dictionary of missing dependencies"
        to_install+=("$pkg")
    fi
done

# Install missing dependencies if any
if [ ${#to_install[@]} -gt 0 ]; then
    log "Installing missing packages: ${to_install[*]}"
    $PKG_MANAGER "${to_install[@]}"

    # Verify installation
    err=0
    for pkg in "${!deps[@]}"; do
        cmd="${deps[$pkg]}"
        if ! command -v "$cmd" &>/dev/null; then
            echo "There was an error installing $pkg (expected command: $cmd)"
            err=1
        fi
    done

    if [[ "$err" -eq 1 ]]; then
        echo "Install these manually"
        echo "Can't continue without dependencies"
        echo "Exiting..."
       cd "$SCRIPTPATH" || exit 1
    fi
else
    log "No need to install packages, all packages where detected"
fi

log "All dependencies installed"
log ""
