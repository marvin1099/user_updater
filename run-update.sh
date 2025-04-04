#!/bin/bash

logfile="/tmp/topgrade-report.log"
uptoml="$HOME/.config/topgrade.toml"

sudo touch "$logfile"
sudo chmod 777 "$logfile" 2>/dev/null
sudo chown root:root "$logfile"

sudo mkdir -p "$HOME/.config"
sudo chown "$USER":"$USER" "$HOME/.config"
sudo chmod u+rwx "$HOME/.config"

g=$(timeout 1 topgrade --edit-config > /dev/null 2>&1)
sudo touch "$uptoml"
sudo chown "$USER":"$USER" "$uptoml"
sudo chmod u+rwx "$uptoml"

misc=0
if cat "$uptoml" | grep "[misc]" > /dev/null; then
    misc=1
fi
if cat "$uptoml" | grep "assume_yes =" > /dev/null; then
    if [[ $misc -eq 1 ]]
    then
        sed -i '/assume_yes =/c\assume_yes = true' "$uptoml" > /dev/null
    else
        sed -i '/assume_yes =/c\[misc]\nassume_yes = true' "$uptoml" > /dev/null
    fi
else
    if [[ $misc -eq 1 ]]
    then
        sed -i '/\[misc\]/c\[misc]\nassume_yes = true' "$uptoml" > /dev/null
    else
        echo "[misc]"$'\n'"assume_yes = true" >> "$uptoml"
    fi
fi

yes | topgrade --no-retry -c > "$logfile" 2>&1

echo "Topgrade finished!" >> "$logfile"
sleep 0.5
sudo rm "$logfile"
