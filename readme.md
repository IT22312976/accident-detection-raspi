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


----


You have a classic "works in shell, silent in service" gap. The cause is almost certainly: your shell's aplay uses a different device than the service's aplay.

The service hardcodes AUDIO_DEVICE=plughw:1,0 at detector.service:16. If you "fixed aplay" by e.g. plugging into a USB speaker or switching output via raspi-config, your shell might now use card 0 (HDMI) or card 2 (USB DAC) — but the service still blindly calls aplay -D plughw:1,0, which ALSA happily accepts and routes to silence or to the wrong output.

Prove this in 2 commands on the Pi:


# 1. Is the service even TRYING to play the sound?
sudo journalctl -u detector.service -b | grep -E "SPEAKER|DETECTION"
You'll see one of:

[SPEAKER] Currently playing aloud: ./audio/startup.wav (Priority 1) → service did call aplay. Silence means wrong device. Go to step 2.
[SPEAKER] Currently playing aloud: ... followed by [SPEAKER] ERROR playing ... rc=... stderr=... → aplay failed. The stderr tells you why. Paste it.
Nothing at all → setup_models() never returned (a model failed to load silently) or run_headless_daemon wasn't reached. Check for Failed to load ... model or Python tracebacks in the full journal.

# 2. Run aplay with EXACTLY the same arguments the service uses.
#    If this is also silent, you've reproduced the bug outside the service.
aplay -q -D plughw:1,0 /home/accidentsystem/Desktop/accident-detection-raspi/audio/startup.wav && echo "HEARD IT" || echo "SILENT"
If step 2 is silent but aplay /path/to/startup.wav (without -D) plays fine, your working device is not plughw:1,0. Find the right one:


aplay -l    # list cards
# Then try each until you hear it:
for c in 0 1 2 3; do echo "--- card $c ---"; aplay -q -D plughw:$c,0 audio/startup.wav && echo "card $c WORKS"; done
Once you know the right card (say plughw:2,0), fix the service. Two options:

Option A — edit the installed unit file directly (quick):


sudo systemctl edit --full detector.service
# change the Environment=AUDIO_DEVICE=plughw:1,0 line to your working device
# save, then:
sudo systemctl daemon-reload
sudo systemctl restart detector.service
Option B — fix the template and re-install (persists across reboots/reinstalls):

Edit detector.service:16 in the project to the correct value, copy it to the Pi, then:


cd /home/accidentsystem/Desktop/accident-detection-raspi
sudo ./install.sh
One more gotcha worth mentioning: if your "fix" for aplay involved PulseAudio or Pipewire, those daemons only run inside a user login session, not inside a systemd system service. The service user (accidentsystem) doesn't have a graphical/user session when running headless, so Pulse/Pipewire aren't available. That's actually why app.py:20-23 deliberately uses aplay (direct ALSA) — it works without a user session. But it requires the right raw ALSA device, which is why AUDIO_DEVICE has to match hardware, not Pulse's default sink.

Want me to make AUDIO_DEVICE overridable at install time so you can do sudo AUDIO_DEVICE=plughw:2,0 ./install.sh and never touch the unit file? Takes two small edits — say yes and I'll do it.