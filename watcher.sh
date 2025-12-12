#!/bin/bash

SCRIPT_TO_RUN="server.py"
WATCH_DIR="." # Or the specific directory containing your script

echo "Watching for changes in $WATCH_DIR to restart $SCRIPT_TO_RUN..."

# Start the Python script initially
python3 "$SCRIPT_TO_RUN" &
PID=$!

# Loop indefinitely, watching for file changes
inotifywait -m -r -e close_write,create,delete --format '%w%f' "$WATCH_DIR" | while read FILE
do
    if [[ "$FILE" ]]; then
        echo "Change detected in $WATCH_DIR. Restarting..."
        kill "$PID" # Terminate the running Python script
        python3 "$SCRIPT_TO_RUN" &
        PID=$!
    fi
done
