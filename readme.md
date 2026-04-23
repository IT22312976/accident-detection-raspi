# 0. Get the latest code onto the Pi (choose ONE of these):
#    If the project is a git clone:
cd /home/accidentsystem/Desktop/accident-detection-raspi
git pull
#    OR if you edit on Mac and transfer manually, rsync/scp the updated
#    install.sh, app.py, and detector.service over first.

# 1. Stop the crash loop and nuke the stale unit file
sudo systemctl stop detector.service
sudo systemctl disable detector.service
sudo rm -f /etc/systemd/system/detector.service
sudo systemctl daemon-reload
sudo systemctl reset-failed detector.service 2>/dev/null || true

# 2. VERIFY you are in the right folder (app.py MUST be listed)
cd /home/accidentsystem/Desktop/accident-detection-raspi
pwd
ls app.py install.sh detector.service   # all three must exist here

# 3. Find the venv (so you know what install.sh will pick up, or can override)
find /home/accidentsystem -maxdepth 5 -name myenv -type d 2>/dev/null

# 4. Re-install (install.sh now pre-flight-checks app.py exists AND the venv works)
sudo ./install.sh
#    If auto-discovery picks the wrong myenv, override it:
# sudo VENV_DIR=/home/accidentsystem/Desktop/accident-detection-raspi/myenv ./install.sh

# 5. CONFIRM the baked unit file has the RIGHT paths — no more AP.HTTP
sudo cat /etc/systemd/system/detector.service | grep -E "WorkingDirectory|ExecStart"
#    Expected:
#    WorkingDirectory=/home/accidentsystem/Desktop/accident-detection-raspi
#    ExecStart=/.../myenv/bin/python /home/accidentsystem/Desktop/accident-detection-raspi/app.py

# 6. Watch it boot
sudo journalctl -u detector.service -f
#    You should see within a few seconds:
#    [BOOT] python     = /.../myenv/bin/python
#    [BOOT] venv_active= True
#    Loading animal model from ...
