#!/bin/bash

SCRIPT=$(readlink -f "$0")
SCRIPTPATH=$(dirname "$SCRIPT")

cd "$SCRIPTPATH" || exit 1

loginfo=$(./main_logger.sh "" "Updater GUI" "User Updater" "${USER}update" "*")
admin_log="$(echo "$loginfo" | head -1)"
log() {
    echo "$1" | sudo tee -a "$admin_log"
}
echo "$loginfo" | tail -n +2

log "Setting monitored file"
LOG_FILE="/tmp/topgrade-report.log"  # Path to the monitored file
PERM_LOG="${admin_log%.*}return.log"

while true
do
    log "Starting Main Loop"
    log "Wait for the logfile \"$LOG_FILE\" to exist with content"
    # Ensure the file exists before running
    while [[ ! -f "$LOG_FILE" ]] || [[ -z "$(cat "$LOG_FILE" 2>/dev/null)" ]]
    do
        sleep 1
    done

    log "Create a pipe for the GUI window to read from"
    # Use a FIFO (named pipe) to ensure YAD gets proper input termination
    PIPE=$(mktemp -u)  # Generate a unique temporary file path
    mkfifo "$PIPE"     # Create the named pipe

    startmsg="Starting Update..."
    echo "$startmsg" | tee "$PIPE" >> "$PERM_LOG" &
    log "Coping the file into the pipe and to permanent storage"
    log "Also Starting tail to write live updates into the pipe and to permanent storage"
    tee "$PIPE" < "$LOG_FILE" >> "$PERM_LOG" &

    # Start tail in the background
    tail -n 0 -f "$LOG_FILE" | tee "$PIPE" >> "$PERM_LOG" &
    TAIL_PID=$!
    log "The command tail is runnig on pid $TAIL_PID"

    log "Setting Update GUI Window margins and size"
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
        log "Checking for xrandr"
        if command -v xrandr >/dev/null 2>&1; then
            log "Command xrandr found"
            log "Getting connected primary display"
            MONITOR_INFO=$(xrandr --current | grep " connected primary")
            if [ -n "$MONITOR_INFO" ]; then
                log "Found connected primary display"
                log "Getting primary display cordinates and size"
                GEOMETRY=$(echo "$MONITOR_INFO" | grep -oE '[0-9]+x[0-9]+\+[0-9]+\+[0-9]+' | head -n 1)
                if [ -n "$GEOMETRY" ]; then
                    log "Successfully extracted screen size and cordinates"
                    log "Calulating window position"
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
            log "Detected successfully calulated window position"
            log "Starting yad in the correct window position"
            # Start YAD in the bottom right and feed it from the pipe
            yad --title="UPDATE IN PROGRESS" --posx="$POS_X" --posy="$POS_Y" --width="$WINDOW_WIDTH" --height="$WINDOW_HEIGHT" --fontname="Monospace" --wrap --text="You can use your computer while the update is running, but\nDO NOT SHUTDOW THE COMPUTER.\nUPDATE IN PROGRESS:" --text-info --tail --no-buttons --no-focus --fixed < "$PIPE" &
            YAD_PID=$!
        else
            log "Could not calulate yad window position"
            log "Starting yad in a neutral window position"
            # Start YAD in neutral position and feed it from the pipe
            yad --title="UPDATE IN PROGRESS" --width="$WINDOW_WIDTH" --height="$WINDOW_HEIGHT" --fontname="Monospace" --wrap --text="You can use your computer while the update is running, but\nDO NOT SHUTDOW THE COMPUTER.\nUPDATE IN PROGRESS:" --text-info --tail --no-buttons --no-focus --fixed < "$PIPE" &
            YAD_PID=$!
        fi

        log "Wait until Logfile is deleted or Yad is closed"
        # Monitor file existence and yad process
        while [[ -f "$LOG_FILE" ]] && ps -p "$YAD_PID" > /dev/null
        do
            # If the file exists or YAD is still running, continue
            sleep 1
        done
        log "Check if Logfile still exists"
        if [[ -f "$LOG_FILE" ]]
        then
            log "Logfile is found"
            log "Therefore yad must be restarted"
            log "The yad while loop will shortly repeat and rerun yad"
            echo "$startmsg $RANDOM" > "$PIPE" &
            cat "$LOG_FILE" > "$PIPE" &
            sleep 0.5
        else
            log "Log file is not found"
            log "While loop will exit shortly"
        fi
        log "Closing yad, if still running"
        kill -9 "$YAD_PID" > /dev/null 2>&1
    done

    log "Updates finished"
    echo "Updates finished, Closing" > "$PIPE" &
    sleep 2

    log "Exiting tail"
    # Cleanup: Stop `tail`, close YAD, and remove pipe
    kill -9 "$TAIL_PID" > /dev/null 2>&1

    log "Removing pipe"
    rm -f "$PIPE"

    log "Making extra shure yad is closed"
    kill -9 "$YAD_PID" > /dev/null 2>&1

    log "End of While loop"
    log "The script will restart shortly to watch for the next updates"
done
