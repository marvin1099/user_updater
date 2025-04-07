#!/bin/bash

echo "Updating user-level tools..."

# Update Flatpak apps (user scope only)
if command -v flatpak >/dev/null; then
    echo "Updating Flatpak (user)..."
    flatpak update --user -y
fi

# Update pipx packages
if command -v pipx >/dev/null; then
    echo "Upgrading pipx packages..."
    pipx upgrade-all
fi

# Update user-installed pip packages
if command -v pip >/dev/null; then
    echo "Upgrading pip user packages..."
    if command -v jq >/dev/null; then
        python3 -m pip list --user --outdated --format=json | jq -r '.[].name' | while read -r pkg; do
            python3 -m pip install --user --upgrade "$pkg"
        done
    else
        # Fallback: parse the human-readable format
        python3 -m pip list --user --outdated | awk 'NR>2 {print $1}' | while read -r pkg; do
            python3 -m pip install --user --upgrade "$pkg"
        done
    fi
fi

# Update user-installed npm global packages
if command -v npm >/dev/null && npm config get prefix | grep -q "$HOME"; then
    echo "Upgrading npm user packages..."
    npm update -g
fi

# Update cargo-installed binaries (Rust)
if command -v cargo >/dev/null; then
    echo "Upgrading cargo-installed tools..."
    if cargo install-update -V >/dev/null 2>&1; then
        cargo install-update -a
    else
        echo "To enable cargo updates run the command:"
        echo "cargo install cargo-update"
    fi
fi

# Update Ruby gems installed in user home
if command -v gem >/dev/null; then
    echo "Upgrading Ruby gems (user)..."
    gem update --user-install
fi

echo "Done updating user-level tools!"
