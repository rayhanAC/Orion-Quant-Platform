#!/bin/bash
# Generic algo launcher — customize for your own bots
# Usage: edit the ALGO and ASSETS below, then: bash start_all.sh

ALGO="python3 path/to/your/bot.py"
ASSETS=("BTC" "ETH" "SOL")
LOG_DIR="./logs"

mkdir -p "$LOG_DIR"

for asset in "${ASSETS[@]}"; do
    echo "Starting bot for $asset ..."
    nohup $ALGO --asset $asset > "$LOG_DIR/${asset,,}.log" 2>&1 &
    sleep 1
done

echo "All bots launched. Check $LOG_DIR/ for logs."
