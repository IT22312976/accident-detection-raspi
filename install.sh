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
# the absolute path to the venv's python. Pointing ExecStart at <venv>/bin/python
# is equivalent to `source activate` for the child process's module resolution.
#
# Resolution order for the venv:
#   1. VENV_DIR env var (explicit override)
#   2. A few well-known default paths (fast check, no disk scan)
#   3. Auto-discover: find any directory named `myenv` that looks like a venv
#      (contains bin/python) under common roots.
RUN_HOME="$(getent passwd "$RUN_USER" | cut -d: -f6)"
RUN_HOME="${RUN_HOME:-/home/${RUN_USER}}"

is_venv() { [[ -x "$1/bin/python" ]]; }

if [[ -z "${VENV_DIR:-}" ]]; then
  # Fast path: check the common locations first.
  for candidate in \
    "${WORKDIR}/myenv" \
    "${RUN_HOME}/AP/myenv" \
    "${RUN_HOME}/myenv" \
    "/opt/myenv"
  do
    if is_venv "$candidate"; then
      VENV_DIR="$candidate"
      break
    fi
  done
fi

if [[ -z "${VENV_DIR:-}" ]]; then
  # Fallback: search common roots for any `myenv` directory that looks like a venv.
  echo "Searching for 'myenv' virtual environment..." >&2
  SEARCH_ROOTS=("$WORKDIR" "$RUN_HOME" "/opt" "/srv" "/home")
  while IFS= read -r -d '' found; do
    if is_venv "$found"; then
      VENV_DIR="$found"
      echo "Found venv at: $VENV_DIR" >&2
      break
    fi
  done < <(find "${SEARCH_ROOTS[@]}" -maxdepth 5 -type d -name myenv -print0 2>/dev/null)
fi

if [[ -z "${VENV_DIR:-}" ]]; then
  VENV_DIR="${WORKDIR}/myenv"  # will fall through to the error check below
fi
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
  echo "ERROR: could not locate a 'myenv' virtual environment." >&2
  echo "Searched: \$VENV_DIR, ${WORKDIR}/myenv, ${RUN_HOME}/AP/myenv, ${RUN_HOME}/myenv, /opt/myenv," >&2
  echo "         and any 'myenv' dir under ${WORKDIR}, ${RUN_HOME}, /opt, /srv, /home (depth 5)." >&2
  echo "Fix one of:" >&2
  echo "  (a) point VENV_DIR at your venv:  sudo VENV_DIR=/path/to/myenv ./install.sh" >&2
  echo "  (b) create the venv at the default path:" >&2
  echo "        cd $WORKDIR && python3 -m venv myenv && source myenv/bin/activate && pip install -r requirements.txt" >&2
  exit 1
fi

echo "Installing $SERVICE_NAME:"
echo "  User        = $RUN_USER"
echo "  WorkingDir  = $WORKDIR"
echo "  VenvDir     = $VENV_DIR"
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
