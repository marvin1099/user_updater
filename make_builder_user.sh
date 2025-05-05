#!/usr/bin/env bash

# Ensure the script is run as root
if [ "$EUID" -ne 0 ]; then
    echo "This script must be run as root. Exiting."
    exit 1
fi

SCRIPT=$(readlink -f "$0")
SCRIPTPATH=$(dirname "$SCRIPT")

cd "$SCRIPTPATH" || exit 1

loginfo=$(./main_logger.sh "" "Make Builder" "Update" "update" "install")
UUPDATER_IDATE=$(echo "$loginfo" | sed -n '1p')
export UUPDATER_IDATE
UUPDATER_ACTION=$(echo "$loginfo" | sed -n '2p')
export UUPDATER_ACTION
admin_log=$(echo "$loginfo" | sed -n '3p')
log() {
    echo "$1" | tee -a "$admin_log"
}
echo "$loginfo" | sed -n '4,$p'

USER_NAME=$USER
log "Going into user while loop."
while id "$USER_NAME" &>/dev/null
do
    if [[ "$USER_NAME" -ne "$USER" ]]
    then
        echo "User '$USER_NAME' already exists."
        rm -r "$TmpHome"
    fi
    TmpHome="$(mktemp -d /tmp/builder.XXXXXXXXXX)"
    USER_NAME="$(basename "$TmpHome")"
done
log "Found possible user \"$USER_NAME\"."

log "Deleting old users."
# Add new user to the userlist and delete old ones
./delete_and_note_users.sh "$USER_NAME"

log "Creating user: \"$USER_NAME\"."
useradd -m -d "$TmpHome" -s /bin/bash "$USER_NAME"
log "User \"$USER_NAME\" created."

log "Giving user all perms for thier home folder."
# give user all perms over the home folder
chown "$USER_NAME":"$USER_NAME" -R "$TmpHome"
chmod u+rwx,g+rwx,o+r -R "$TmpHome"

# Lock the user's password to prevent direct login
log "Locking password for user \"$USER_NAME\"."
passwd -l "$USER_NAME"

SUDOERS_FILE="/etc/sudoers.d/${USER_NAME/./\-}"

log "Removing any existing sudoers file for the user."
# Create a sudoers file for the user
if [ -f "$SUDOERS_FILE" ]; then
    echo "Sudoers file '$SUDOERS_FILE' already exists. Deleting."
    rm "$SUDOERS_FILE"
fi

log "Adding user to the docker group."
# Add to docker for container updates
if getent group docker; then
    usermod -a -G docker "$USER_NAME"
fi

log "Making shure the wheel group exists."
# Make sure the wheel group exists
getent group wheel >/dev/null || groupadd wheel

log "Adding builder user to wheel group."
# Then add the user to the group so updates like flatpaks work
usermod -a -G wheel "$USER_NAME"

log "Adding sudoers \"$SUDOERS_FILE\" file for user."
echo "${USER_NAME} ALL=(ALL) NOPASSWD: ALL" > "$SUDOERS_FILE"
chmod 440 "$SUDOERS_FILE"  # Ensure correct permissions
log "Sudoers file created for user \"$USER_NAME\"."

log "Setup complete."
log "User is ready to use."
echo "$USER_NAME"
