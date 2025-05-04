#!/bin/bash

# Ensure the script is run as root
if [ "$EUID" -ne 0 ]; then
    echo "This script must be run as root. Exiting."
    exit 1
fi

SCRIPT=$(readlink -f "$0")
SCRIPTPATH=$(dirname "$SCRIPT")

cd "$SCRIPTPATH" || exit 1

loginfo=$(./main_logger.sh "" "Update" "update" "update")
UUPDATER_IDATE=$(echo "$loginfo" | sed -n '1p')
export UUPDATER_IDATE
UUPDATER_ACTION=$(echo "$loginfo" | sed -n '2p')
export UUPDATER_ACTION
admin_log=$(echo "$loginfo" | sed -n '3p')
log() {
    echo "$1" | tee -a "$admin_log"
}
echo "$loginfo" | sed -n '4,$p'

log "Setting config file"
config_file="user_updater.conf"

stop_inst="$1"
upda="$2"
fupda="$3"
sfile="$4"
if [[ -n "$upda" ]]; then upda="true"; else upda="false"; fi
if [[ -n "$fupda" ]]; then upda="true"; else fupda="false"; fi
if [[ -n "$sfile" ]]; then upda="true"; else sfile="false"; fi

log "Setting config dictionary"
# Default entries
declare -A entrys=(
    ["self update"]="$upda # This makes the script update itself"
    ["forced self update"]="$fupda # This will force a update even if local changes are incompatible"
    ["reactivate service file"]="$sfile # If this is enabled the sytemd service will be reactivated after update"
)

log "Creating config file"
touch "$config_file"

log "Reading the config"
for i in $(seq 1 2); do
    # Collected keys from config
    declare -a keys=()

    # Parse config
    while IFS= read -r line; do
        # Strip inline comments
        line="${line%%#*}"

        # Trim leading/trailing whitespace
        line="${line#"${line%%[![:space:]]*}"}"
        line="${line%"${line##*[![:space:]]}"}"

        [[ -z "$line" ]] && continue

        # Split into key and value
        IFS='=' read -r key value <<< "$line"

        # Normalize and lowercase
        key="${key,,}"
        value="${value,,}"

        # Trim again (in case of spaces around =)
        key="${key%"${key##*[![:space:]]}"}" # trims left of key
        value="${value#"${value%%[![:space:]]*}"}" # trims right of value

        [[ -z "$key" || -z "$value" ]] && continue

        # Store matching keys
        keys+=("$key")

        # Detect enabled values
        no_space_key="${key//[[:space:]]/}"
        if [[ "$value" == "true" && $i -eq 2 ]]; then
            case "$no_space_key" in
                "selfupdate") up=1 ;;
                "forcedselfupdate") fup=1 ;;
                "reactivateservicefile") rsf=1 ;;
            esac
        fi
    done < "$config_file"

    # Append defaults if missing
    if [[ $i -eq 1 ]]; then
        for entry in "${!entrys[@]}"; do
            found=0
            for existing in "${keys[@]}"; do
                tr_key="${existing//[[:space:]]/}"
                tr_ent="${entry//[[:space:]]/}"
                if [[ "$tr_key" == "$tr_ent" ]]; then
                    found=1
                    break
                fi
            done

            if [[ $found -eq 0 ]]; then
                value=${entrys[$entry]}
                e="${entry%%#*}"
                if [[ "$e" -ne "$entry" ]]
                then
                    if [[ "${entry:0:1}" == "#" ]]
                    then
                        echo "$entry $value" >> "$config_file"
                    else
                        echo "#$entry $value" >> "$config_file"
                    fi
                else
                    echo "$entry = $value" >> "$config_file"
                fi
            fi
        done
    fi
done

log "Config was read"

if [[ -z $stop_inst ]]; then
    if [[ -n $fup ]]
    then
        log "Self Update was requested to be forced. Forcing update..."
        up=1
        git reset --hard origin
    fi
    if [[ -n $up ]]
    then
        log "Self Update was requested"
        if [[ -n $rsf ]]; then
            log "Service was requested to be reactivated. Deleting old service file."
            SERVICE_FILE="/etc/systemd/system/user_updater.service"
            if [[ -f "$SERVICE_FILE" ]]; then
                rm -f "$SERVICE_FILE"
            fi
        fi

        log "Update complete, rerunning the installer, to update and reregister"
        # Make shure no sudo user is set to avid dobble updates and or infinite loops
        export SUDO_USER=
        ./install.sh
    else
        log "Self Update was disabled. Only Reregistering new users..."
        ./register_updater_gui.sh
    fi
else
    log "Self Updater was set only create the config"
fi
