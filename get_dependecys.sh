#!/bin/bash

# Ensure the script is run as root
if [ "$EUID" -ne 0 ]; then
    echo "This script must be run as root. Exiting."
    exit 1
fi

# List of packages to install
deps=("git","awk","sudo","topgrade","yad")

# Detect package manager
if command -v pacman &>/dev/null; then
    PKG_MANAGER="pacman -S --noconfirm"
elif command -v apt &>/dev/null; then
    PKG_MANAGER="apt install -y"
elif command -v dnf &>/dev/null; then
    PKG_MANAGER="dnf install -y"
elif command -v zypper &>/dev/null; then
    PKG_MANAGER="zypper install -y"
elif command -v brew &>/dev/null; then
    PKG_MANAGER="brew install"
elif command -v apk &>/dev/null; then
    PKG_MANAGER="apk add"
elif command -v emerge &>/dev/null; then
    PKG_MANAGER="emerge"
elif command -v xbps-install &>/dev/null; then
    PKG_MANAGER="xbps-install -y"
elif command -v pkg &>/dev/null; then
    PKG_MANAGER="pkg install -y"
else
    echo "Unsupported package manager. Please install manually."
    exit 1
fi

# Collect missing dependencies
to_install=()
for pkg in "${deps[@]}"; do
    if command -v "$pkg" &>/dev/null; then
        echo "$pkg is already installed. Skipping..."
    else
        to_install+=("$pkg")
    fi
done

# Install missing dependencies if any
if [ ${#to_install[@]} -gt 0 ]; then
    echo "Installing missing packages: ${to_install[*]}"
    $PKG_MANAGER ${to_install[@]}
fi

err=0
for pkg in "${deps[@]}"; do
    if ! command -v "$pkg" &>/dev/null; then
        echo "There was a error installing $pkg"
        err=1
    fi
done
if [[ "$err" -eq 1 ]]
then
    echo "Install these manually"
    echo "Can't continue without dependencies"
    echo "Exiting..."
    exit 1
fi

echo "All dependencies installed."
