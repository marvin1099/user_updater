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
    yes | topgrade --no-retry -c > "$logfile" 2>&1 &
    pid=$!

    while kill -0 $pid 2>/dev/null; do
        sleep 10
        last_modified=$(stat -c %Y "$logfile")
        now=$(date +%s)
        diff=$((now - last_modified))

        if (( diff > TIMEOUT_SECONDS )); then
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
    done
    return 0
}

# Try with retries
attempt=1
while (( attempt <= MAX_RETRIES )); do
    run_with_watchdog $attempt && break
    attempt=$((attempt + 1))
    echo "Retrying... ($attempt/$MAX_RETRIES)"
done

echo "Topgrade system updates finished!" >> "$logfile"
g_user=$(./find_gui_user.sh)
if [[ -n "$g_user" ]]
then
    echo "Updating user tools..." > "$logfile"
    sudo -u "$g_user" "/home/$g_user/.config/user_updater/update_user_tools.sh" > "$logfile" 2>&1
    echo "User tool updates done" > "$logfile"
else
    echo "No GUI user was found." > "$logfile"
    echo "Skipping user tool updates" > "$logfile"
fi

sleep 0.5
sudo rm "$logfile"
