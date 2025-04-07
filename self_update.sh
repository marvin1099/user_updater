#!/bin/bash

# Ensure the script is run as root
if [ "$EUID" -ne 0 ]; then
    echo "This script must be run as root. Exiting."
    exit 1
fi

SCRIPT=$(readlink -f $0)
SCRIPTPATH=$(dirname "$SCRIPT")

cd "$SCRIPTPATH"

config_file="user_updater.conf"

# Default entries
declare -A entrys=(
    ["self update"]="true # This makes the script update itself"
    ["forced self update"]="true # This will force a update even if local changes are incompatible"
)

touch "$config_file"

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
            esac
        fi
    done < "$config_file"

    # Append defaults if missing
    if [[ $i -eq 1 ]]; then
        for entry in "${!entrys[@]}"; do
            found=0
            for existing in "${keys[@]}"; do
                tr_key="${existing//[[:space:]]/}"
                ent="${entry,,}"
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

if [[ -n $fup ]]
then
    up=1
    git reset --hard origin
fi
if [[ -n $up ]]
then
    # Make shure no sudo user is set to avid dobble updates and or infinite loops
    export SUDO_USER=
    ./install.sh
else
    ./register_updater_gui.sh
fi
