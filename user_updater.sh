#!/bin/bash

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
    export UUPDATER_IDATE="$(date '+%F_%H-%M-%S')"
    uuset=1
fi
if [[ -z "$UUPDATER_ACTION" ]] || [[ "$UUPDATER_ACTION" == "install" ]]; then
    export UUPDATER_ACTION="selfupdate"
fi
admin_log="$log_dir/${UUPDATER_IDATE}_$UUPDATER_ACTION.log"
touch "$admin_log"
chmod 664 "$admin_log"
log() {
    echo "$1" | tee -a "$admin_log"
}
if [[ -z "$uuset" ]]; then
    echo "Logs are saved to \"$log_dir\""
    log "Starting Self Update log at $UUPDATER_IDATE"
else
    log ""
fi
log "Starting  script"

SCRIPT=$(readlink -f "$0")
SCRIPTPATH=$(dirname "$SCRIPT")

cd "$SCRIPTPATH"

loginfo=$(./main_logger.sh "" "Main Update" "Self Update" "selfupdate" "install")
admin_log="$(echo "$loginfo" | head -1)"
log() {
    echo "$1" | tee -a "$admin_log"
}
echo "$(echo "$loginfo" | tail -n +2)"

log "Running self update"
./self_update.sh

log "Getting builder users"
# Get builder user
builder_usernames="builder_usernames.txt"
err=0
while read j
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
