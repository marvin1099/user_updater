#!/bin/bash

# Ensure the script is run as root
if [ "$EUID" -ne 0 ]; then
    echo "This script must be run as root. Exiting."
   cd "$SCRIPTPATH" || exit 1
fi

SCRIPT=$(readlink -f "$0")
SCRIPTPATH=$(dirname "$SCRIPT")

cd "$SCRIPTPATH" || exit 1

loginfo=$(./main_logger.sh "" "Main Update" "Self Update" "selfupdate" "install")
admin_log="$(echo "$loginfo" | head -1)"
log() {
    echo "$1" | tee -a "$admin_log"
}
echo "$loginfo" | tail -n +2

log "Running self update"
./self_update.sh

log "Getting builder users"
# Get builder user
builder_usernames="builder_usernames.txt"
err=0
while read -r j
do
    if [[ -n "$j" ]]
    then
        if id "$j" &>/dev/null
        then
            user="$j"
            log "Found previus builder user \"$user\" to use"
            userdir="/tmp/$user"
            mkdir -p "$userdir"
            err=0
            break
        else
            err=1
        fi
    fi
done < "$builder_usernames"

if [[ $err -eq 1 ]] || [[ -z $user ]]
then
    log "No valid builder user found, making a new builder user"
    out=$(./make_builder_user.sh)
    user=$(echo "$out" | tail -1)
fi

log "Build user \"$user\" is ready to use, running updates on user"
sudo -u "$user" ./run_update.sh

log "Updates should be finished, deleteing builder user \"$user\" in 30 seconds"
sleep 30; ./delete_and_note_users.sh
