#!/bin/bash

# Ensure the script is run as root
if [ "$EUID" -ne 0 ]; then
    echo "This script must be run as root. Exiting."
    exit 1
fi

SCRIPT=$(readlink -f "$0")
SCRIPTPATH=$(dirname "$SCRIPT")

cd "$SCRIPTPATH" || exit 1

loginfo=$(./main_logger.sh "" "Delete old build users" "Update" "update")
UUPDATER_IDATE=$(echo "$loginfo" | sed -n '1p')
export UUPDATER_IDATE
UUPDATER_ACTION=$(echo "$loginfo" | sed -n '2p')
export UUPDATER_ACTION
admin_log=$(echo "$loginfo" | sed -n '3p')
log() {
    echo "$1" | tee -a "$admin_log"
}
echo "$loginfo" | sed -n '4,$p'

log "Seting builder usernames storage file"
BUsers="/var/lib/user_updater/builder_usernames.txt"

log "Making the parrent directory of the usernames storage file"
mkdir -p "$(dirname $BUsers)"

log "Creating builder usernames file \"$BUsers\""
touch "$BUsers"

log "Reading the file line by line and delete any found user"
while read -r p; do
    if [[ -n $p ]]
    then
        log "Checking for user \"$p\""
        if id "$p" &>/dev/null
        then
            log "Found user. Deleting..."
            userdel -f -r "$p"
        fi

        HomeDir="/tmp/$p"
        #echo $HomeDir
        log "Checking for home directory \"$HomeDir\""
        if [[ -d "$HomeDir" ]]
        then
            log "Found home directory. Deleting..."
            rm -r "$HomeDir"
        fi

        RM_SUDOERS_FILE="/etc/sudoers.d/${p/./\-}"
        log "Checking for sudoers file \"$RM_SUDOERS_FILE\""
        if [[ -f "$RM_SUDOERS_FILE" ]]
        then
            log "Found sudoers file. Deleting..."
            rm "$RM_SUDOERS_FILE"
        fi
    fi
done < "$BUsers"
log "Deleting builder usernames file"
rm "$BUsers"
log "Touching builder usernames file"
touch "$BUsers"

log "Adding given arguments as users to builder usernames file"
for var in "$@"
do
    if [[ -n "$var" ]]; then
        log "Got user \"$var\""
        log "Adding user to builder usernames file"
        echo "$var" >> "$BUsers"
    fi
done
log "Finished deleting builders and noting new ones"
log ""
