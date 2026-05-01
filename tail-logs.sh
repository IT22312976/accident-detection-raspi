#!/usr/bin/env bash
# Follow the detector logs live.
#   ./tail-logs.sh        # follow ./logs/detector.log (works without root)
#   ./tail-logs.sh -j     # follow systemd journal (journalctl -u detector.service -f)
#   ./tail-logs.sh -s     # show service status, then follow the file
set -euo pipefail

cd "$(dirname "$0")"

LOG_FILE="logs/detector.log"

case "${1:-}" in
    -j|--journal)
        exec journalctl -u detector.service -f
        ;;
    -s|--status)
        systemctl status detector.service --no-pager || true
        echo
        ;;
esac

if [[ ! -f "$LOG_FILE" ]]; then
    echo "Waiting for $LOG_FILE to appear (is the service running?)..."
    while [[ ! -f "$LOG_FILE" ]]; do sleep 1; done
fi

# -F follows the file across rotations (RotatingFileHandler renames on rollover).
exec tail -n 50 -F "$LOG_FILE"
