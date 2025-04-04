#!/bin/bash

# Ensure the script is run as root
if [ "$EUID" -ne 0 ]; then
    echo "This script must be run as root. Exiting."
    exit 1
fi

SCRIPT=$(readlink -f "$0")
SCRIPTPATH=$(dirname "$SCRIPT")

cd "$SCRIPTPATH"

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
    echo "Error, builder user not found."
    echo "Please rerun the installer."
    exit 1
fi

# wait 5 minutes for a gui user
gui_user=$(timeout 300 ./find_gui_user.sh)

if [[ -n $gui_user ]]
then
    # Run the user gui
    chmod 777 ./gui-report.sh
    IFS=' ' read -r display guser <<< "$gui_user"
    sudo -u "$guser" ./gui-report.sh "$display" "$guser"
fi

sudo -u "$user" ./run-update.sh
