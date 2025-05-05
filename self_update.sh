#!/usr/bin/env bash

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

log "Setting config file."
config_file="user_updater.conf"

stop_inst="$1"

# Optional override setters for existing config (leave unset or empty to skip)
set_up="$2"
set_fup="$3"
set_rsf="$4"

# Default values for first-time setup (when config file doesn't exist)
default_up=true
default_fup=true
default_rsf=true

# Default entries and their comments
declare -A default_entries=(
    ["self update"]="# This makes the script update itself"
    ["forced self update"]="# This will force an update even if local changes are incompatible"
    ["reactivate service file"]="# If enabled, the systemd service will be reactivated after update"
)

# Internal key-value map from parsed config
declare -A values=()
declare -a keys=()

# Function to trim whitespace and remove comments for parsing (but preserve in file)
trim() {
    local input="$1"
    # Get a copy without comments for parsing
    parse_input="${input%%#*}"
    parse_input="${parse_input#"${parse_input%%[![:space:]]*}"}"  # Trim leading space
    parse_input="${parse_input%"${parse_input##*[![:space:]]}"}"  # Trim trailing space
    echo "$parse_input"
}

# Parse configuration file into internal data structures
parse_config() {
    # Reset arrays
    keys=()
    values=()

    if [[ ! -f "$config_file" ]]; then
        log "Config file not found, creating new one."
        touch "$config_file"
        return
    fi

    while IFS= read -r line; do
        line_for_parsing=$(trim "$line")
        [[ -z "$line_for_parsing" ]] && continue

        IFS='=' read -r key value <<< "$line_for_parsing"
        key=$(trim "${key,,}")
        value=$(trim "${value,,}")

        [[ -z "$key" || -z "$value" ]] && continue

        keys+=("$key")
        values["$key"]="$value"
    done < "$config_file"
}

# Add any missing default entries to the config file
append_missing_defaults() {
    for entry in "${!default_entries[@]}"; do
        norm_entry="${entry//[[:space:]]/}"
        found=0

        for k in "${keys[@]}"; do
            if [[ "${k//[[:space:]]/}" == "$norm_entry" ]]; then
                found=1
                break
            fi
        done

        if [[ $found -eq 0 ]]; then
            # Choose the default value based on which entry we're setting
            default_value="false"
            if [[ "$entry" == "self update" ]]; then
                default_value="$default_up"
            elif [[ "$entry" == "forced self update" ]]; then
                default_value="$default_fup"
            elif [[ "$entry" == "reactivate service file" ]]; then
                default_value="$default_rsf"
            fi

            echo "$entry = $default_value # ${default_entries[$entry]#\# }" >> "$config_file"
        fi
    done
}

# Update a specific entry in the config file
update_config_entry() {
    local key="$1"
    local new_value="$2"

    # Skip if no value was provided
    [[ -z "$new_value" ]] && return

    local temp_file
    temp_file=$(mktemp)
    local updated=0

    while IFS= read -r line; do
        # Check if this is a line for our key
        if [[ "${line%%#*}" =~ ^[[:space:]]*${key,,}[[:space:]]*= ]]; then
            # Extract any comments from the original line
            comment=""
            if [[ "$line" == *"#"* ]]; then
                comment=" #${line##*#}"
            fi
            echo "$key = $new_value$comment" >> "$temp_file"
            updated=1
        else
            echo "$line" >> "$temp_file"
        fi
    done < "$config_file"

    # If the entry wasn't found, add it
    if [[ $updated -eq 0 ]]; then
        local comment_text=""
        # Check if we have a default comment for this entry
        if [[ -n "${default_entries[$key]}" ]]; then
            comment_text=" # ${default_entries[$key]#\# }"
        fi
        echo "$key = $new_value$comment_text" >> "$temp_file"
    fi

    mv "$temp_file" "$config_file"
}

# Apply the setter variables to the config
apply_setters() {
    [[ -n "$set_up" ]] && update_config_entry "self update" "$set_up"
    [[ -n "$set_fup" ]] && update_config_entry "forced self update" "$set_fup"
    [[ -n "$set_rsf" ]] && update_config_entry "reactivate service file" "$set_rsf"
}

# Update internal flags based on config values
update_internal_flags() {
    # Initialize with default values
    up=0
    fup=0
    rsf=0

    # Set flags based on config values
    [[ "${values[self update]}" == *"t"* ]] && up=1
    [[ "${values[forced self update]}" == *"t"* ]] && fup=1
    [[ "${values[reactivate service file]}" == *"t"* ]] && rsf=1
}

# === Main ===
log "Reading the config."
parse_config
log "Adding missing defaults."
append_missing_defaults
log "Setting values if requested."
apply_setters

if [[ -z $stop_inst ]]; then
    log "Rereading config to see if changes where made."
    parse_config  # Re-parse to reflect any changes made by setters
    log "Setting variables."
    update_internal_flags

    if [[ -n $fup ]]
    then
        log "Self Update was requested to be forced. Forcing update..."
        up=1
        git reset --hard origin
    fi
    if [[ -n $up ]]
    then
        log "Self Update was requested."
        if [[ -n $rsf ]]; then
            log "Service was requested to be reactivated. Deleting old service file."
            SERVICE_FILE="/etc/systemd/system/user_updater.service"
            if [[ -f "$SERVICE_FILE" ]]; then
                rm -f "$SERVICE_FILE"
            fi
        fi

        log "Update complete, rerunning the installer, to update and reregister."
        # Make shure no sudo user is set to avid dobble updates and or infinite loops
        export SUDO_USER=
        ./install.sh
    else
        log "Self Update was disabled. Only Reregistering new users..."
        ./register_updater_gui.sh
    fi
else
    log "Self Updater was set only read and write to the config. Closing subscript..."
fi
