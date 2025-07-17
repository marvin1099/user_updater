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
log "Ensuring config directory \"$HOME/.config\"."
sudo mkdir -p "$HOME/.config"
sudo chown "$USER":"$USER" "$HOME/.config"
sudo chmod u+rwx "$HOME/.config"

# Check if topgrade command is avalible
if ! command -v topgrade &> /dev/null; then
    log "Error: 'topgrade' is not installed or not in PATH."
    exit 1
fi

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
if grep "\[misc\]" "$uptoml" >/dev/null; then
    misc=1
fi
if grep "assume_yes =" "$uptoml" >/dev/null; then
    if [[ $misc -eq 1 ]]; then
        sed -i '/assume_yes =/c\assume_yes = true' "$uptoml"
    else
        sed -i '/assume_yes =/c\[misc]\n\nassume_yes = true' "$uptoml"
    fi
else
    if [[ $misc -eq 1 ]]; then
        sed -i '/\[misc\]/c\[misc]\n\nassume_yes = true' "$uptoml"
    else
        echo $'[include]\n\n[misc]\n\nassume_yes = true\n...' >> "$uptoml"
    fi
fi

# Clean up and filter topgrade output
filter_output() {
  sed -u \
    -e '/^[[:space:]]*[yY][[:space:]]*$/d' \
    -e 's/\x1B\[[0-9;]*[a-zA-Z]//g' \
    -e 's/\r//g' \
    -e 's/�//g'
}

# Function to remove lingering auto-pacman proesses
kill_lingering() {
    # Capture auto-pacman PIDs after topgrade finishes
    local after_pids
    after_pids=$(pgrep -f auto-pacman || true)

    # Find new auto-pacman processes started during topgrade
    local new_pids
    new_pids=$(comm -13 <(echo "$before_pids" | sort) <(echo "$after_pids" | sort))

    if [[ -n "$new_pids" ]]; then
        log "Detected lingering auto-pacman processes started by topgrade: $new_pids. Attempting to kill them..."
        for pid in $new_pids; do
            sudo kill -9 "$pid"
        done
        log "Killed lingering auto-pacman processes."
    else
        log "No lingering auto-pacman processes detected after update."
    fi
}

# Function to run topgrade with monitoring and retry logic
run_with_watchdog() {
    local attempt=$1
    before_pids=$(pgrep -f auto-pacman || true)
    log "Starting topgrade attempt $attempt..."
    {
        yes | topgrade --cleanup --no-retry 2>&1 | filter_output
    } >> "$logfile" &
    local pid=$!
    local msg_nr=0
    local divider=1

    while kill -0 "$pid" 2>/dev/null; do
        sleep 10
        last_modified=$(stat -c %Y "$logfile")
        now=$(date +%s)
        diff=$((now - last_modified))

        if (( diff > TIMEOUT_SECONDS / divider )); then
            if (( attempt < MAX_RETRIES )); then
                log "Detected potential stalemate. Killing topgrade (PID $pid)..."
                sudo kill -9 "$pid" 2>/dev/null
                sleep 1
                if kill -0 "$pid" 2>/dev/null; then
                    log "Failed to kill topgrade (PID $pid)."
                else
                    log "Successfully killed stuck topgrade (PID $pid)."
                fi
                kill_lingering

                return 1
            else
                # Last attempt: notify manual update but keep watching
                msg_nr=$((msg_nr + 1))
                if [[ $msg_nr -eq 1 ]]; then
                    ms=$'\nThe update seems to take longer than expected.'
                    ms+=$'\nIf this keeps showing up, you may need to update manually.\n'
                elif [[ $msg_nr -eq 2 ]]; then
                    ms=$'\nThe update won\'t seem to finish, START A MANUAL UPDATE.'
                    ms+=$'\nYou can update manually by running your distro\'s update command.\n'
                else
                    ms=""
                fi
                if [[ -n "$ms" ]]; then
                    TIMEOUT_SECONDS=20
                    log "$ms" | tee -a "$logfile"
                fi
                divider=2
                # Continue monitoring without killing
            fi
        fi
    done

    # If we reach here, topgrade exited
    wait "$pid" 2>/dev/null
    if [[ $? -eq 0 ]]; then
        log "Topgrade attempt $attempt finished successfully."
    else
        log "Topgrade attempt $attempt exited with errors."
    fi
    kill_lingering

    return 0
}

user_tools_update() {
    if [[ -z "$USER_TOOLS_DONE" ]]; then
        # USER_TOOLS_DONE=1
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
    fi
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

user_tools_update

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

user_tools_update

log ""
log "Removing Logfile."
sleep 0.5
sudo rm "$logfile"
log "Finished all updates and cleanup."
log ""
