#!/bin/bash

# Ensure the script is run as root
if [ "$EUID" -ne 0 ]; then
    echo "This script must be run as root. Exiting."
    exit 1
fi

install_dir="/var/lib/user_updater"
if [[ -f "get_dependencies.sh" ]] && [[ "$(pwd)" != "$install_dir" ]]
then
    ./get_dependencies.sh
fi

git=0
cd "$install_dir"
if [[ $? -eq 0 ]]
then
    if git rev-parse --is-inside-work-tree 2> /dev/null
    then
        git pull 2> /dev/null # 2>&1
        git=1
    fi
fi

cd "$(dirname "$install_dir")"

if [[ $git -eq 0 ]]
then


    if ! git clone https://codeberg.org/marvin1099/user_updater
    then
        if ! git clone https://github.com/marvin1099/user_updater
        then
            echo "Error downloding the repo. Exiting..."
            sleep 1
            exit 1
        fi
    fi
fi

cd "$install_dir"
./get_dependencies.sh
if [[ $? -ne 0 ]]
then
    exit 1
fi

# Make builder user if not avalible
builder_usernames="builder_usernames.txt"
retryed=0
while [[ -z $user ]] || ! id "$user" &>/dev/null
do
    if [[ $retryed -eq 1 ]]
    then
        ./make_builder_user.sh
    fi

    while read j
    do
        if id "$j" &>/dev/null
        then
            user="$j"
            userdir="/tmp/$user"
            mkdir -p "$userdir"
            break
        fi
    done < "$builder_usernames"

    retryed=1
done

./register_systemd.sh

./register_updater_gui.sh
