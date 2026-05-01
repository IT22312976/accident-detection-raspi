import os

os.environ["QT_QPA_PLATFORM"] = "offscreen"
import matplotlib

matplotlib.use("Agg")

from ultralytics import YOLO
import cv2
import numpy as np
import threading
import queue
import time
import subprocess
import platform
import signal
import sys
import logging
from logging.handlers import RotatingFileHandler


# Logs go to stdout (captured by systemd → `journalctl -u detector.service -f`)
# and to a rotating file at ./logs/detector.log so anyone can tail them without
# root. Override level with LOG_LEVEL=DEBUG, location with LOG_DIR=/path.
LOG_DIR = os.environ.get(
    "LOG_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs"),
)
LOG_FILE = os.path.join(LOG_DIR, "detector.log")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()


def setup_logging():
    os.makedirs(LOG_DIR, exist_ok=True)
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(threadName)-9s | %(name)-9s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    root = logging.getLogger()
    root.setLevel(LOG_LEVEL)
    root.handlers.clear()

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    root.addHandler(stream)

    fileh = RotatingFileHandler(
        LOG_FILE, maxBytes=5_000_000, backupCount=5, encoding="utf-8"
    )
    fileh.setFormatter(fmt)
    root.addHandler(fileh)

    logging.getLogger("ultralytics").setLevel(logging.WARNING)


setup_logging()
log_boot = logging.getLogger("BOOT")
log_speaker = logging.getLogger("SPEAKER")
log_detect = logging.getLogger("DETECT")
log_models = logging.getLogger("MODELS")
log_stream = logging.getLogger("STREAM")
log_daemon = logging.getLogger("DAEMON")


# Use aplay on Linux (ALSA, talks directly to /dev/snd/* — no user session
# needed, so it works under a systemd system service at boot). On macOS, use
# afplay. Set AUDIO_DEVICE to the ALSA device, e.g. plughw:1,0; find cards
# with `aplay -l`. Use plughw: (not hw:) so ALSA resamples if the wav's rate
# doesn't match the hardware.
AUDIO_DEVICE = os.environ.get("AUDIO_DEVICE", "")


def _build_audio_cmd(audio_file):
    if platform.system() == "Darwin":
        return ["afplay", audio_file]
    cmd = ["aplay", "-q"]
    if AUDIO_DEVICE:
        cmd.extend(["-D", AUDIO_DEVICE])
    cmd.append(audio_file)
    return cmd


class AudioAlerter:
    def __init__(self, cooldown=5.0):
        self.q = queue.PriorityQueue()
        self.cooldown = cooldown
        self.last_played = {}
        self.audio_thread = threading.Thread(target=self._worker, daemon=True)
        self.audio_thread.start()

    def _worker(self):
        while True:
            item = self.q.get()
            if item is None:
                break

            priority, timestamp, alert_class = item

            audio_file = f"./audio/{alert_class}.wav"
            if os.path.exists(audio_file):
                log_speaker.info(
                    "Playing %s (priority=%s)", audio_file, priority
                )
                try:
                    cmd = _build_audio_cmd(audio_file)
                    result = subprocess.run(cmd, capture_output=True, text=True)
                    if result.returncode != 0:
                        log_speaker.error(
                            "Failed to play %s via %s: rc=%s stderr=%s",
                            audio_file, cmd[0], result.returncode,
                            result.stderr.strip(),
                        )
                except FileNotFoundError as e:
                    log_speaker.error("Audio player not installed: %s", e)
                except Exception as e:
                    log_speaker.exception("Audio playback failed: %s", e)
            else:
                log_speaker.warning("Audio file not found: %s", audio_file)
            self.q.task_done()

    def queue_alert(self, alert_class):
        now = time.time()
        # Cooldown check
        if alert_class in self.last_played:
            if now - self.last_played[alert_class] < self.cooldown:
                return  # Skip to avoid spam/loop

        log_detect.warning(
            "🚨 Detected threat: %s — triggering audio system", alert_class
        )
        self.last_played[alert_class] = now

        # Priority mapping (Lower number = plays first)
        priority = 2 if alert_class == "tws" else 1

        # Cooldown inherently prevents infinite queue growth, so we safely put all unique threats.
        self.q.put((priority, now, alert_class))


# Initialize global alerter
audio_alerter = AudioAlerter(cooldown=5.0)

# Define paths for models
MODELS_DIR = "./models"
MODEL_PATHS = {
    "animal": os.path.join(MODELS_DIR, "best_animal_detector_v5.pt"),
    "overtake": os.path.join(MODELS_DIR, "best_overtake_detector_v3.pt"),
    "tws": os.path.join(MODELS_DIR, "best_tws_detector_v3.pt"),
    "road": os.path.join(MODELS_DIR, "road_detection.pt"),
}

# USB camera device indices. On Raspberry Pi, each UVC camera exposes two
# /dev/video* nodes (video + metadata), so two physical cams typically land
# on indices 0 and 2. Override via env vars if your hardware differs.
CAM_MAIN_INDEX = int(os.environ.get("CAM_MAIN_INDEX", "0"))
CAM_TWS_INDEX = int(os.environ.get("CAM_TWS_INDEX", "2"))

_stop_event = threading.Event()

models = {}


def setup_models():
    """Load the YOLO models."""
    global models

    # Restrict OpenCV threads for Raspberry Pi efficiency
    cv2.setNumThreads(4)

    for model_name, pt_path in MODEL_PATHS.items():
        # Check if an NCNN model directory exists
        ncnn_dir = pt_path.replace(".pt", "_ncnn_model")
        path_to_load = ncnn_dir if os.path.exists(ncnn_dir) else pt_path

        if os.path.exists(path_to_load):
            try:
                log_models.info("Loading %s model from %s ...", model_name, path_to_load)
                task_type = "segment" if model_name == "road" else "detect"
                models[model_name] = YOLO(path_to_load, task=task_type)
                log_models.info("%s model loaded successfully", model_name)
            except Exception as e:
                log_models.exception(
                    "Failed to load %s model from %s: %s", model_name, path_to_load, e
                )
        else:
            log_models.error("Model file not found: %s", path_to_load)


def run_stream(cam_index, model_names, apply_road_mask, stream_label, stop_event):
    """Run one camera stream through a subset of the loaded models until stop_event is set.

    Cam A uses {road, animal, overtake} with road_mask filtering; cam B uses {tws} alone.
    These subsets are disjoint, so no shared YOLO object is invoked from both threads.
    """
    log_stream.info("[%s] Opening camera at index %s ...", stream_label, cam_index)
    cap = cv2.VideoCapture(cam_index, cv2.CAP_V4L2)

    if not cap.isOpened():
        log_stream.error(
            "[%s] Failed to open camera at index %s", stream_label, cam_index
        )
        return

    # Force MJPEG + low resolution. Two USB cams on a Pi 4 share one USB 2.0
    # controller, so raw YUYV at default resolution exceeds bandwidth and V4L2
    # select() times out. MJPEG is compressed; 640x480 is enough for YOLO at
    # imgsz=640.
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    cap.set(cv2.CAP_PROP_FPS, 15)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    log_stream.info("[%s] Negotiated %sx%s", stream_label, actual_w, actual_h)

    # Enforce 6 FPS logic per stream
    video_fps = cap.get(cv2.CAP_PROP_FPS)
    if video_fps <= 0 or np.isnan(video_fps):
        video_fps = 30.0

    target_fps = 6.0
    frame_skip = max(1, int(round(video_fps / target_fps)))
    frame_count = 0

    if apply_road_mask:
        target_models_sorted = ["road"] + [m for m in model_names if m != "road"]
    else:
        target_models_sorted = list(model_names)

    log_stream.info(
        "[%s] Active — models=%s @ %s FPS",
        stream_label, target_models_sorted, target_fps,
    )

    try:
        while not stop_event.is_set():
            ret, frame = cap.read()
            if not ret:
                log_stream.warning(
                    "[%s] Stream disconnected or lost — waiting", stream_label
                )
                time.sleep(1)
                continue

            if frame_count % frame_skip == 0:
                annotated_frame = frame.copy()
                road_mask = None

                for model_name in target_models_sorted:
                    if model_name in models:
                        model = models[model_name]

                        if model_name == "road":
                            results = model(
                                annotated_frame,
                                verbose=False,
                                conf=0.20,
                                classes=[1],
                                imgsz=640,
                            )

                            road_mask = np.zeros(
                                annotated_frame.shape[:2], dtype=np.uint8
                            )
                            has_road = False
                            if results[0].masks is not None:
                                for m in results[0].masks.xy:
                                    cv2.fillPoly(road_mask, [np.int32(m)], 255)
                                    has_road = True
                            elif (
                                results[0].boxes is not None
                                and len(results[0].boxes) > 0
                            ):
                                for box in results[0].boxes.xyxy:
                                    x1, y1, x2, y2 = map(int, box[:4])
                                    cv2.rectangle(
                                        road_mask, (x1, y1), (x2, y2), 255, -1
                                    )
                                    has_road = True

                            if not has_road:
                                road_mask.fill(255)
                        else:
                            results = model(annotated_frame, verbose=False, imgsz=640)

                            if (
                                apply_road_mask
                                and model_name in ["animal", "overtake"]
                                and road_mask is not None
                            ):
                                filtered_indices = []
                                if results[0].boxes is not None:
                                    for i, box in enumerate(results[0].boxes):
                                        x1, y1, x2, y2 = box.xyxy[0].tolist()
                                        cx, cy = int((x1 + x2) / 2), int(y2)
                                        cx = max(0, min(cx, road_mask.shape[1] - 1))
                                        cy = max(0, min(cy, road_mask.shape[0] - 1))

                                        mcx, mcy = (
                                            int((x1 + x2) / 2),
                                            int((y1 + y2) / 2),
                                        )
                                        mcx = max(0, min(mcx, road_mask.shape[1] - 1))
                                        mcy = max(0, min(mcy, road_mask.shape[0] - 1))

                                        if (
                                            road_mask[cy, cx] > 0
                                            or road_mask[mcy, mcx] > 0
                                        ):
                                            filtered_indices.append(i)
                                    results[0] = results[0][filtered_indices]

                        if model_name in ["animal", "overtake", "tws"]:
                            for result in results:
                                if result.boxes is not None and len(result.boxes) > 0:
                                    audio_alerter.queue_alert(model_name)

            frame_count += 1

    finally:
        cap.release()
        log_stream.info("[%s] Camera released", stream_label)


def run_headless_daemon():
    """Spawn one detection thread per USB camera and orchestrate clean shutdown."""
    log_daemon.info("Initiating dual-camera stream daemon")

    # Audible confirmation that models are loaded and detection is starting.
    audio_alerter.queue_alert("startup")

    def _shutdown(sig, frame):
        log_daemon.info("Shutdown signal %s received — stopping camera streams", sig)
        _stop_event.set()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGTSTP, _shutdown)

    t_main = threading.Thread(
        target=run_stream,
        args=(CAM_MAIN_INDEX, ["road", "animal", "overtake"], True, "cam-main", _stop_event),
        name="cam-main",
    )
    t_tws = threading.Thread(
        target=run_stream,
        args=(CAM_TWS_INDEX, ["tws"], False, "cam-tws", _stop_event),
        name="cam-tws",
    )
    t_main.start()
    t_tws.start()

    while not _stop_event.is_set():
        _stop_event.wait(timeout=0.5)
        # Exit if both workers have died so systemd can restart the service.
        if not t_main.is_alive() and not t_tws.is_alive():
            log_daemon.error(
                "Both camera streams exited — shutting down for systemd to restart"
            )
            _stop_event.set()

    t_main.join(timeout=5)
    t_tws.join(timeout=5)
    log_daemon.info("Both streams released — exiting")
    sys.exit(0)


if __name__ == "__main__":
    # Surface runtime context so `journalctl -u detector.service` and
    # ./logs/detector.log prove the venv is active and paths are correct
    # without attaching a debugger.
    log_boot.info("python      = %s", sys.executable)
    log_boot.info("sys.prefix  = %s", sys.prefix)
    log_boot.info("venv_active = %s", sys.prefix != sys.base_prefix)
    log_boot.info("cwd         = %s", os.getcwd())
    log_boot.info("log_file    = %s (level=%s)", LOG_FILE, LOG_LEVEL)
    setup_models()
    run_headless_daemon()
