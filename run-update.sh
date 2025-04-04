#!/bin/bash

uptoml="$HOME/.config/topgrade.toml"
logfile="/tmp/topgrade-report.log"

sudo touch "$logfile"
sudo chmod 777 "$logfile" 2>/dev/null

sudo touch "$uptoml"
misc=0
if sudo cat "$uptoml" | grep "[misc]" > /dev/null; then
    misc=1
fi
if sudo cat "$uptoml" | grep "assume_yes =" > /dev/null; then
    if [[ $misc -eq 1 ]]
    then
        sudo sed -i '/assume_yes =/c\assume_yes = true' "$uptoml" > /dev/null
    else
        sudo sed -i '/assume_yes =/c\[misc]\nassume_yes = true' "$uptoml" > /dev/null
    fi
else
    if [[ $misc -eq 1 ]]
    then
        sudo sed -i '/\[misc\]/c\[misc]\nassume_yes = true' "$uptoml" > /dev/null
    else
        sudo echo "[misc]"$'\n'"assume_yes = true" >> "$uptoml"
    fi
fi

yes | topgrade --no-retry -c > "$logfile" 2>&1

echo "Topgrade finished!" >> "$logfile"
sleep 0.5
rm "$logfile"
