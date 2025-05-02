#!/bin/bash

logfile="/tmp/topgrade-report.log"
uptoml="$HOME/.config/topgrade.toml"
MAX_RETRIES=3
TIMEOUT_SECONDS=60

# Prepare log file
sudo touch "$logfile"
sudo chmod 777 "$logfile" 2>/dev/null
sudo chown root:root "$logfile"

# Ensure topgrade config directory exists
sudo mkdir -p "$HOME/.config"
sudo chown "$USER":"$USER" "$HOME/.config"
sudo chmod u+rwx "$HOME/.config"

# Generate config if missing
timeout 1 topgrade --edit-config > /dev/null 2>&1
sudo touch "$uptoml"
sudo chown "$USER":"$USER" "$uptoml"
sudo chmod u+rwx "$uptoml"

# Enable assume_yes in config
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

# Function to run topgrade with monitoring
run_with_watchdog() {
    echo "Starting topgrade attempt $1..."
    yes | topgrade --no-retry -c >> "$logfile" 2>&1 &
    pid=$!
    msg_nr=0

    while kill -0 $pid 2>/dev/null; do
        sleep 10
        last_modified=$(stat -c %Y "$logfile")
        now=$(date +%s)
        diff=$((now - last_modified))

        if (( diff > TIMEOUT_SECONDS )); then
            if [[ $1 -eq $MAX_RETRIES ]]; then
                msg_nr=$((msg_nr + 1))
                if [[ $msg_nr -eq 1 ]]; then
                    echo "" >> "$logfile"
                    echo "The update seems to take longer then expected" >> "$logfile"
                    echo "If this message keeps showing up you may need to manually update" >> "$logfile"
                    echo "" >> "$logfile"
                elif [[ $msg_nr -eq 10 ]]; then
                    echo "" >> "$logfile"
                    echo "The update is still not finished please consider manually updating" >> "$logfile"
                    echo "" >> "$logfile"
                elif [[ $msg_nr -eq 20 ]]; then
                    echo "" >> "$logfile"
                    echo "The update won't seem to finsh, START A MANUAL UPDATE" >> "$logfile"
                    echo "If you don not know how to update manually ask you system admin or websearch:" >> "$logfile"
                    name=$(cat /etc/os-release | awk -F'NAME=' '/NAME/ {print substr($2,2,length($2)-2)}' | head -1) #' 
                    echo "How to update $name in the terminal" >> "$logfile"
                    echo "Then open you terminal an paste the command you found online and hit enter" >> "$logfile"
                    echo "You may need to enter you password enter to start the installation and confirm by pressing Y and Enter" >> "$logfile"
                    echo "Reboot after the manual update is done" >> "$logfile"
                    echo "" >> "$logfile"
                elif [[ $msg_nr -gt 28 ]]; then
                    msg_nr=19
                fi
            else
                echo "Detected potential stalemate. Killing topgrade (PID $pid)..."
                sudo kill -9 $pid 2>/dev/null
                sleep 1
                if sudo kill -0 $pid 2>/dev/null; then
                    echo "Failed to kill topgrade (PID $pid)."
                else
                    echo "Successfully killed stuck topgrade (PID $pid)."
                fi
                return 1
            fi
        fi
    done
    return 0
}

# Wait for any user to login except root
while true; do
  if who | awk '{ if ($1 != "root") print $1 }' | head -1; then
    echo "User detected. Proceeding..."
    break
  fi
  sleep 5
done

# Get the gui user, if any for later
g_user=$(timeout 30 ./find_gui_user.sh | tail -1) # This is here to enshure the user is logged in
if [[ -n "$g_user" ]]
then
    sleep 10 # start 10 seconds after the user is logged in
fi

# Try with retries
attempt=1
while (( attempt <= MAX_RETRIES )); do
    run_with_watchdog $attempt && break
    attempt=$((attempt + 1))
    echo "Retrying... ($attempt/$MAX_RETRIES)"
done

echo "Topgrade system updates finished!" >> "$logfile"

if [[ -z "$g_user" ]]
then
    g_user=$(who | awk '{ if ($1 != "root") print $1 }' | head -1) #'
fi

# If there was a gui user update their tools
if [[ -n "$g_user" ]]
then
    echo "Updating user tools..." >> "$logfile"
    sudo -u "$g_user" "/home/$g_user/.config/user_updater/update_user_tools.sh" >> "$logfile" 2>&1
    echo "User tool updates done" >> "$logfile"
else
    echo "No logged in user was found." >> "$logfile"
    echo "Skipping user tool updates" >> "$logfile"
fi

sleep 0.5
sudo rm "$logfile"
