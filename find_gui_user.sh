#!/bin/bash

while true
do
    # Check for an Xorg or Wayland session
    ttys=$(ps aux | awk '/Xorg|wayland/ && !/awk/ {if (length($7) > 1) print $7}')

    # Find the user associated with the TTY
    if [[ -n "$ttys" ]]
    then
        while IFS= read -r tty
        do
            user=$(who | awk -v tty="$tty" '$2 == tty {print $1}')
            display=$(who | awk -v tty="$tty" '$2 == tty {print substr($5, 2, length($5) - 2)}')
            if [[ -n "$user" ]]
            then
                #echo "User $user is using a graphical session."
                break
            fi
        done <<< "$ttys"
        if [[ -n "$user" ]]
        then
            break
        fi
    fi
    sleep 1
done
echo $display $user
