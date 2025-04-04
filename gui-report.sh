#!/bin/bash

export DISPLAY=$1
export XAUTHORITY="/home/$2/.Xauthority"

LOG_FILE="/tmp/topgrade-report.log"  # Path to the monitored file

# Ensure the file exists before running
touch "$LOG_FILE"
chmod 777 "$logfile" 2>/dev/null

# Use a FIFO (named pipe) to ensure YAD gets proper input termination
PIPE=$(mktemp -u)  # Generate a unique temporary file path
mkfifo "$PIPE"     # Create the named pipe

startmsg="Starting Update..."
echo "$startmsg" > "$PIPE" &
cat "$LOG_FILE" > "$PIPE" &

# Start tail in the background
tail -n 0 -f "$LOG_FILE" > "$PIPE" &
TAIL_PID=$!

NEWLINE=$'\n'

while [[ -f "$LOG_FILE" ]]
do
    # Start YAD and feed it from the pipe
    yad --title="UPDATE IN PROGRESS" --width=600 --height=400 --fontname="Monospace" --wrap --text="You can use your computer while the update is running, but\nDO NOT SHUTDOW THE COMPUTER.\nUPDATE IN PROGRESS:" --text-info --tail --no-buttons --fixed < "$PIPE" &
    YAD_PID=$!

    # Monitor file existence and yad process
    while [[ -f "$LOG_FILE" ]] && ps -p "$YAD_PID" > /dev/null
    do
        # If the file exists or YAD is still running, continue
        sleep 1
    done
    if [[ -f "$LOG_FILE" ]]
    then
        echo "$startmsg" > "$PIPE" &
        cat "$LOG_FILE" > "$PIPE" &
    fi
done

if [[ -f "$LOG_FILE" ]]
then
    rm "$LOG_FILE"
fi

echo "Update finished, Closing" > "$PIPE" &
sleep 2

# Cleanup: Stop `tail`, close YAD, and remove pipe
if ps -p "$TAIL_PID" > /dev/null
then
    kill "$TAIL_PID"
fi
rm -f "$PIPE"
if ps -p "$YAD_PID" > /dev/null
then
    kill "$YAD_PID"
fi
