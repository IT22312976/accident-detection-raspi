#!/usr/bin/env bash
# Install the detection daemon as a systemd service so it autostarts on boot.
# Run this ONCE on the Raspberry Pi, from the project directory:
#     cd /home/pi/AP.HTTP
#     sudo ./install.sh
set -euo pipefail

SERVICE_NAME="detector.service"
TEMPLATE="$(dirname "$(readlink -f "$0")")/${SERVICE_NAME}"
TARGET="/etc/systemd/system/${SERVICE_NAME}"

WORKDIR="$(dirname "$(readlink -f "$0")")"
RUN_USER="${SUDO_USER:-$USER}"

# Use the pre-created venv's Python (so the service has ultralytics/cv2/ncnn).
# systemd runs the binary directly — it does NOT need the venv activated, just
# the absolute path to the venv's python.
VENV_DIR="${WORKDIR}/myenv"
PYTHON_BIN="${VENV_DIR}/bin/python"

if [[ $EUID -ne 0 ]]; then
  echo "ERROR: must run as root (use sudo)." >&2
  exit 1
fi

if [[ ! -f "$TEMPLATE" ]]; then
  echo "ERROR: $TEMPLATE not found." >&2
  exit 1
fi

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "ERROR: venv Python not found at $PYTHON_BIN" >&2
  echo "Create the venv first: cd $WORKDIR && python3 -m venv myenv && source myenv/bin/activate && pip install -r requirements.txt" >&2
  exit 1
fi

echo "Installing $SERVICE_NAME:"
echo "  User        = $RUN_USER"
echo "  WorkingDir  = $WORKDIR"
echo "  Python      = $PYTHON_BIN"
echo

sed \
  -e "s|__USER__|${RUN_USER}|g" \
  -e "s|__WORKDIR__|${WORKDIR}|g" \
  -e "s|__PYTHON__|${PYTHON_BIN}|g" \
  "$TEMPLATE" > "$TARGET"

chmod 644 "$TARGET"

# Ensure the run user is in the groups needed for cameras and audio.
usermod -aG video,audio "$RUN_USER" || true

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"

echo
echo "Installed. Useful commands:"
echo "  sudo systemctl status  $SERVICE_NAME"
echo "  sudo journalctl -u $SERVICE_NAME -f"
echo "  sudo systemctl restart $SERVICE_NAME"
echo "  sudo systemctl disable $SERVICE_NAME   # to stop autostart"
