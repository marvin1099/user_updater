#!/bin/bash

# Ensure the script is run as root
if [ "$EUID" -ne 0 ]; then
    echo "This script must be run as root. Exiting."
    exit 1
fi

SCRIPT=$(readlink -f "$0")
SCRIPTPATH=$(dirname "$SCRIPT")

cd "$SCRIPTPATH"

./register_updater_gui.sh

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
            userdir="/tmp/$user"
            mkdir -p "$userdir"
            break
        else
            err=1
        fi
    fi
done < "$builder_usernames"

if [[ $err -eq 1 ]] || [[ -z $user ]]
then
    out=$(./make_builder_user.sh)
    user=$(echo "$out" | tail -1)
fi

echo "Got build user $user"
sudo -u "$user" ./run-update.sh

sleep 30; ./delete_and_note_users.sh
