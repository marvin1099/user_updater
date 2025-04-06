#!/bin/bash

while true
do
    LOG_FILE="/tmp/topgrade-report.log"  # Path to the monitored file

    # Ensure the file exists before running
    touch "$LOG_FILE"
    chmod 777 "$LOG_FILE" 2>/dev/null

    while [[ -z "$(cat "$LOG_FILE")" ]]
    do
        sleep 1
    done

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

    MARGIN_LEFT=100
    MARGIN_BOTTOM=160
    WINDOW_WIDTH=600
    WINDOW_HEIGHT=400
    MONITOR_X=
    MONITOR_Y=
    SCREEN_WIDTH=
    SCREEN_HEIGHT=
    while [[ -f "$LOG_FILE" ]]
    do
        if command -v xrandr >/dev/null 2>&1; then
            MONITOR_INFO=$(xrandr --current | grep " connected primary")
            if [ -n "$MONITOR_INFO" ]; then
                GEOMETRY=$(echo "$MONITOR_INFO" | grep -oE '[0-9]+x[0-9]+\+[0-9]+\+[0-9]+' | head -n 1)
                if [ -n "$GEOMETRY" ]; then
                    MONITOR_X=$(echo "$GEOMETRY" | cut -d'+' -f2)
                    MONITOR_Y=$(echo "$GEOMETRY" | cut -d'+' -f3)
                    SCREEN_WIDTH=$(echo "$GEOMETRY" | cut -d'x' -f1)
                    SCREEN_HEIGHT=$(echo "$GEOMETRY" | cut -d'x' -f2 | cut -d'+' -f1)
                    POS_X=$((MONITOR_X + SCREEN_WIDTH - WINDOW_WIDTH - MARGIN_LEFT))
                    POS_Y=$((MONITOR_Y + SCREEN_HEIGHT - WINDOW_HEIGHT - MARGIN_BOTTOM))
                fi
            fi
        fi
        if [[ -n "$MONITOR_X" && -n "$MONITOR_Y" && -n "$SCREEN_WIDTH" && -n "$SCREEN_HEIGHT" ]]
        then
            # Start YAD in the bottom right and feed it from the pipe
            yad --title="UPDATE IN PROGRESS" --posx=$POS_X --posy=$POS_Y --width=$WINDOW_WIDTH --height=$WINDOW_HEIGHT --fontname="Monospace" --wrap --text="You can use your computer while the update is running, but\nDO NOT SHUTDOW THE COMPUTER.\nUPDATE IN PROGRESS:" --text-info --tail --no-buttons --fixed < "$PIPE" &
            YAD_PID=$!
        else
            # Start YAD in neutral position and feed it from the pipe
            yad --title="UPDATE IN PROGRESS" --width=$WINDOW_WIDTH --height=$WINDOW_HEIGHT --fontname="Monospace" --wrap --text="You can use your computer while the update is running, but\nDO NOT SHUTDOW THE COMPUTER.\nUPDATE IN PROGRESS:" --text-info --tail --no-buttons --fixed < "$PIPE" &
            YAD_PID=$!
        fi

        # Monitor file existence and yad process
        while [[ -f "$LOG_FILE" ]] && ps -p "$YAD_PID" > /dev/null
        do
            # If the file exists or YAD is still running, continue
            sleep 1
        done
        if [[ -f "$LOG_FILE" ]]
        then
            echo "$startmsg $RANDOM" > "$PIPE" &
            cat "$LOG_FILE" > "$PIPE" &
        fi
    done

    if [[ -f "$LOG_FILE" ]]
    then
        rm "$LOG_FILE"
    fi

    echo "Updates finished, Closing" > "$PIPE" &
    sleep 2

    # Cleanup: Stop `tail`, close YAD, and remove pipe
    if ps -p "$TAIL_PID" > /dev/null
    then
        kill -9 "$TAIL_PID"
    fi
    rm -f "$PIPE"
    if ps -p "$YAD_PID" > /dev/null
    then
        kill -9 "$YAD_PID"
    fi
done
