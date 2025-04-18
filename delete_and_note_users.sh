#!/bin/bash

# Ensure the script is run as root
if [ "$EUID" -ne 0 ]; then
    echo "This script must be run as root. Exiting."
    exit 1
fi

BUsers="/var/lib/user_updater/builder_usernames.txt"

mkdir -p "$(dirname $BUsers)"
touch "$BUsers"

while read p; do
    if [[ -n $p ]]
    then
        #echo "Checking for user $p"
        if id "$p" &>/dev/null
        then
            userdel -f -r "$p"
        fi

        HomeDir="/tmp/$p"
        #echo $HomeDir
        if [[ -d "$HomeDir" ]]
        then
            rm -r "$HomeDir"
        fi

        RM_SUDOERS_FILE="/etc/sudoers.d/${p/./\-}"
        if [[ -f "$RM_SUDOERS_FILE" ]]
        then
            rm "$RM_SUDOERS_FILE"
        fi
    fi
done < "$BUsers"
rm "$BUsers"
touch "$BUsers.t"
cp "$BUsers.t" "$BUsers"
rm "$BUsers.t"

for var in "$@"
do
    if [[ -n "$var" ]]; then
        echo "$var" >> "$BUsers"
    fi
done
