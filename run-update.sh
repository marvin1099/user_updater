#!/bin/bash

logfile="/tmp/topgrade-report.log"
uptoml="$HOME/.config/topgrade.toml"

sudo touch "$logfile"
sudo chmod 777 "$logfile" 2>/dev/null
sudo chown root:root "$logfile"

sudo mkdir -p "$HOME/.config"
sudo chown "$USER":"$USER" "$HOME/.config"
sudo chmod u+rwx "$HOME/.config"

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
        sed -i '/assume_yes =/c\[misc]\n\nassume_yes = true' "$uptoml" > /dev/null
    fi
else
    if [[ $misc -eq 1 ]]
    then
        sed -i '/\[misc\]/c\[misc]\n\nassume_yes = true' "$uptoml" > /dev/null
    else
        echo $'[include]\n\n[misc]\n\nassume_yes = true\n\n[pre_commands]\n\n[post_commands]\n\n[commands]\n\n[python]\n\n[composer]\n\n[brew]\n\n[linux]\n\n[git]\n\n[windows]\n\n[npm]\n\n[yarn]\n\n[deno]\n\n[vim]\n\n[firmware]\n\n[vagrant]\n\n[flatpak]\n\n[distrobox]\n\n[containers]\n\n[lensfun]\n\n[julia]' >> "$uptoml"
    fi
fi

yes | topgrade --no-retry -c > "$logfile" 2>&1

echo "Topgrade finished!" >> "$logfile"
sleep 0.5
sudo rm "$logfile"
