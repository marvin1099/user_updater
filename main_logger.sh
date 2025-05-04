#!/bin/bash

# Admin log setup
log_dir="$1"
script_string="$2"
action_string="$3"
action="$4"
onf_action="$5"
ons_action="$6"

if [[ -z "$log_dir" ]]; then
    log_dir="/var/lib/user_updater/logs"
fi
if [[ -z "$action" ]]; then
    action="update"
fi
if [[ -z "$action_string" ]]; then
    action_string="Update"
fi
if [[ -z "$ons_script_string" ]]; then
    script_string="Main Update"
fi

mkdir -p "$log_dir"
chmod a+wr "$log_dir"
if [[ -z "$UUPDATER_IDATE" ]]; then
    UUPDATER_IDATE="$(date '+%F_%H-%M-%S')"
    uuset=1
fi
echo "$UUPDATER_IDATE"
if [[ -z "$UUPDATER_ACTION" ]] || [[ "$UUPDATER_ACTION" == "$onf_action" ]] || [[ "$UUPDATER_ACTION" == "$ons_action" ]] || [[ "$onf_action" == "*" ]]
then
    UUPDATER_ACTION="$action"
fi
echo "$UUPDATER_ACTION"
admin_log="$log_dir/${UUPDATER_IDATE}_$UUPDATER_ACTION.log"
touch "$admin_log"
chmod 664 "$admin_log"
log() {
    echo "$1" | tee -a "$admin_log"
}
echo "$admin_log"
if [[ -z "$uuset" ]]; then
    echo "Logs are saved to \"$log_dir\""
    log "Starting $action_string log at $UUPDATER_IDATE"
else
    log ""
fi
log "Starting $script_string script"

