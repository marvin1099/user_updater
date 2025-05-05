#!/usr/bin/env bash

SCRIPT=$(readlink -f "$0")
SCRIPTPATH=$(dirname "$SCRIPT")

cd "$SCRIPTPATH" || exit 1

loginfo=$(sudo UUPDATER_IDATE="$UUPDATER_IDATE" UUPDATER_ACTION="$UUPDATER_ACTION" ./main_logger.sh "" "Updater" "Updater" "update" "install")
UUPDATER_IDATE=$(echo "$loginfo" | sed -n '1p')
export UUPDATER_IDATE
UUPDATER_ACTION=$(echo "$loginfo" | sed -n '2p')
export UUPDATER_ACTION
admin_log=$(echo "$loginfo" | sed -n '3p')
log() {
    echo "$1" | sudo tee -a "$admin_log"
}
echo "$loginfo" | sed -n '4,$p'

logfile="/tmp/topgrade-report.log"
uptoml="$HOME/.config/topgrade.toml"
MAX_RETRIES=3
TIMEOUT_SECONDS=60

# Prepare log file
log "Preparing Update logfile \"$logfile\"."
sudo touch "$logfile"
sudo chmod 777 "$logfile" 2>/dev/null
sudo chown root:root "$logfile"

# Ensure topgrade config directory exists
log "Enshuring config directory \"$HOME/.config\"."
sudo mkdir -p "$HOME/.config"
sudo chown "$USER":"$USER" "$HOME/.config"
sudo chmod u+rwx "$HOME/.config"

# Generate config if missing
log "Generating the topgrade config \"$uptoml\"."
if [[ ! -f "$uptoml" ]]; then
    topgrade --config-reference > "$uptoml"
fi
sudo touch "$uptoml"
sudo chown "$USER":"$USER" "$uptoml"
sudo chmod u+rwx "$uptoml"

# Enable assume_yes in config
log "Setting \"assume_yes\" to \"true\" in config."
misc=0
if grep "[misc]" "$uptoml" > /dev/null; then
    log "The \"[misc]\" section was found in config."
    misc=1
fi
if grep "assume_yes =" "$uptoml" > /dev/null; then
    log "String \"assume_yes\" was found in config."
    if [[ $misc -eq 1 ]]
    then
        log "Replacing \"assume_yes\" line to \"assume_yes = true\"."
        sed -i '/assume_yes =/c\assume_yes = true' "$uptoml" > /dev/null
    else
        log "Replacing \"assume_yes\" line to \"[misc] \n assume_yes = true\"."
        sed -i '/assume_yes =/c\[misc]\n\nassume_yes = true' "$uptoml" > /dev/null
    fi
else
    if [[ $misc -eq 1 ]]
    then
        log "Replacing \"[misc]\" line to \"[misc] \n assume_yes = true\"."
        sed -i '/\[misc\]/c\[misc]\n\nassume_yes = true' "$uptoml" > /dev/null
    else
        log "No valid config found adding \"[misc] \n assume_yes = true\" as well as other sections used by topgrade."
        echo $'[include]\n\n[misc]\n\nassume_yes = true\n\n[pre_commands]\n\n[post_commands]\n\n[commands]\n\n[python]\n\n[composer]\n\n[brew]\n\n[linux]\n\n[git]\n\n[windows]\n\n[npm]\n\n[yarn]\n\n[deno]\n\n[vim]\n\n[firmware]\n\n[vagrant]\n\n[flatpak]\n\n[distrobox]\n\n[containers]\n\n[lensfun]\n\n[julia]' >> "$uptoml"
    fi
fi

# Function to run topgrade with monitoring
run_with_watchdog() {
    log "Starting topgrade attempt $1..."
    yes | topgrade --cleanup --no-retry >> "$logfile" 2>&1 &
    pid=$!
    msg_nr=0
    divider=1

    while kill -0 $pid 2>/dev/null; do
        sleep 10
        last_modified=$(stat -c %Y "$logfile")
        now=$(date +%s)
        diff=$((now - last_modified))

        if (( diff > (TIMEOUT_SECONDS / divider) )); then
            if [[ $1 -eq $MAX_RETRIES ]]; then
                msg_nr=$((msg_nr + 1))
                if [[ $msg_nr -eq 1 ]]; then
                    ms=$'\n'"The update seems to take longer then expected."
                    ms+=$'\n'"If this message keeps showing up you may need to manually update."$'\n'
                    log "$ms" | tee -a "$logfile"
                    divider=2
                elif [[ $msg_nr -eq 2 ]] || [[ $msg_nr -eq 3 ]]; then
                    ms=$'\n'"The update won't seem to finsh, START A MANUAL UPDATE."
                    ms+=$'\n'"If you don not know how to update manually ask you system admin or websearch:"
                    name=$(awk -F'NAME=' '/NAME/ {print substr($2,2,length($2)-2)}' /etc/os-release | head -1)
                    ms+=$'\n'"How to update $name in the terminal."
                    ms+=$'\n'"Then open you terminal an paste the command you found online and hit enter."
                    ms+=$'\n'"You may need to enter you password enter to start the installation and confirm by pressing Y and Enter."
                    ms+=$'\n'"Reboot after the manual update is done."$'\n'
                    echo "$ms" >> "$logfile"
                    if [[ $msg_nr -eq 2 ]]; then
                        log "$ms"
                    fi
                elif [[ $msg_nr -gt 4 ]]; then
                    msg_nr=2
                    divider=1
                fi
            else
                log "Detected potential stalemate. Killing topgrade (PID $pid)..."
                sudo kill -9 $pid 2>/dev/null
                sleep 1
                if sudo kill -0 $pid 2>/dev/null; then
                    log "Failed to kill topgrade (PID $pid)."
                else
                    log "Successfully killed stuck topgrade (PID $pid)."
                fi
                return 1
            fi
        fi
    done
    return 0
}

log "Waiting for any user to login except root."
# Wait for any user to login except root
while true; do
  u="$(who | awk '{ if ($1 != "root") print $1 }' | head -1)"
  if [[ -n "$u" ]]; then
    log "User \"$u\" detected. Proceeding..."
    break
  fi
  sleep 5
done

log "Trying to find any active gui user."
# Get the gui user, if any for later
fg_user=$(timeout 30 ./find_gui_user.sh) # This is here to enshure the user is logged in
cg_user=$?
g_user=$(echo "$fg_user" | tail -1)
log "$(echo "$fg_user" | head -n -1)"

if [[ -n "$g_user" ]] && [[ "$cg_user" == 0 ]]; then
    log "Found gui user \"$g_user\", starting update in 20 seconds."
    sleep 20 # start 20 seconds after the user is logged in
else
    log "GUI User not found."
    log "Using last logged in user."
    g_user="$u"
fi

# Try with retries
attempt=1
log "Running update with watchdog." | tee -a "$logfile"
log "System update report log is saved at \"$logfile\"."
echo "" >> "$logfile"
while (( attempt <= MAX_RETRIES )); do
    run_with_watchdog $attempt && break
    attempt=$((attempt + 1))
    echo "" >> "$logfile"
    log "Retrying... ($attempt/$MAX_RETRIES)." | tee -a "$logfile"
    echo "" >> "$logfile"
done

echo "" >> "$logfile"
log "Topgrade system updates finished!" | tee -a "$logfile"

# If there was a gui user update their tools
if [[ -n "$g_user" ]]; then
    log "" | tee -a "$logfile"
    log "Got \"$g_user\", updating their user tools."
    log "User tools update report log is saved at \"$logfile\"."
    echo "Updating user tools..." >> "$logfile"
    echo "" >>  "$logfile"
    sudo -u "$g_user" "/home/$g_user/.config/user_updater/update_user_tools.sh" 2>&1 | tee -a "$logfile" > /dev/null
    echo "" >>  "$logfile"
    log "User tool updates done." | tee -a "$logfile"
else
    log "" | tee -a "$logfile"
    log "No logged in user was found." | tee -a "$logfile"
    log "Skipping user tool updates." | tee -a "$logfile"
fi

log ""
log "Removing Logfile."
sleep 0.5
sudo rm "$logfile"
log "Finished all updates and cleanup."
log ""
