#!/bin/bash

# Ensure the script is run as root
if [ "$EUID" -ne 0 ]; then
    echo "This script must be run as root. Exiting."
    exit 1
fi

SCRIPT=$(readlink -f $0)
SCRIPTPATH=$(dirname "$SCRIPT")

USER_NAME=$USER
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

# Add new user to the userlist and delete old ones
$SCRIPTPATH/delete_and_note_users.sh "$USER_NAME"

echo "Creating user: $USER_NAME"
useradd -m -d "$TmpHome" -s /bin/bash "$USER_NAME"
echo "User '$USER_NAME' created."

# give user all perms over the home folder
chown $USER_NAME:$USER_NAME -R "$TmpHome"
chmod u+rwx,g+rwx,o+r -R "$TmpHome"

# Lock the user's password to prevent direct login
echo "Locking password for user '$USER_NAME'"
passwd -l "$USER_NAME"

SUDOERS_FILE="/etc/sudoers.d/${USER_NAME/./\-}"

# Create a sudoers file for the user
echo "Setting up sudoers file: $SUDOERS_FILE"
if [ -f "$SUDOERS_FILE" ]; then
    echo "Sudoers file '$SUDOERS_FILE' already exists. Deleting."
    rm "$SUDOERS_FILE"
fi

# Add to docker for container updates
if getent group docker
then
    usermod -a -G docker "$USER_NAME"
fi

# Make sure the wheel group exists
getent group wheel >/dev/null || groupadd wheel

# Then add the user to the group so updates like flatpaks work
usermod -a -G wheel "$USER_NAME"

echo "${USER_NAME} ALL=(ALL) NOPASSWD: ALL" > "$SUDOERS_FILE"
chmod 440 "$SUDOERS_FILE"  # Ensure correct permissions
echo "Sudoers file created for user '$USER_NAME'."

echo "Setup complete."
echo "User is ready to use."
echo "$USER_NAME"
