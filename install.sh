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

./register_systemd.sh

./register_updater_gui.sh

if [[ -n "$SUDO_USER" ]]; then
    report_gui="/home/$SUDO_USER/.config/user_updater/gui_report.sh"
    yad --text="Testing if Gui output is available" --no-buttons --timeout=1 --no-focus --undecorated --posx 0 --posy 0 --width=350 --height=40
    rep=$?
    echo "The test Gui reported a exit code of $rep"
    if [[ $rep -eq 70 && -f "$report_gui" ]]; then
        g_pid=$(ps aux | awk '/gui_report.sh/ && !/awk/ {if ($1 == "'$SUDO_USER'" && $11 ~ "bash" && $12 ~ "user_updater/gui_report.sh") print $2}' | head -n 1) #'
        if [[ -n $g_pid ]]; then
            kill -9 $g_pid
        fi
        sudo -u "$SUDO_USER" "$report_gui" & disown
        systemctl start user_updater.service
    fi
fi

