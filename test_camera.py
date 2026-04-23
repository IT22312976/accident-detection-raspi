import cv2
import numpy as np
import threading
import time

# We import the required objects from app.py.
# NOTE: We do not import the top offscreen GUI parameters since we WANT a screen to popup here.
from app import (
    setup_models,
    models,
    audio_alerter,
    CAM_MAIN_INDEX,
    CAM_TWS_INDEX,
)


def run_stream_windowed(
    cam_index,
    model_names,
    apply_road_mask,
    stream_label,
    stop_event,
    frame_slot,
    slot_lock,
):
    """Inference worker thread. Writes the latest annotated frame to a shared slot.

    cv2.imshow must run on the main thread (required on macOS Cocoa, reliable elsewhere),
    so this worker does not touch any GUI functions.
    """
    print(f"[{stream_label}] Opening camera at index {cam_index}...")
    cap = cv2.VideoCapture(cam_index)

    if not cap.isOpened():
        print(f"[{stream_label}] ERROR: Failed to open camera at index {cam_index}.")
        return

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

    print(f"[{stream_label}] Active. Models: {target_models_sorted} @ {target_fps} FPS")

    try:
        while not stop_event.is_set():
            ret, frame = cap.read()
            if not ret:
                print(f"[{stream_label}] Stream disconnected.")
                break

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
                                conf=0.10,
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

                        annotated_frame = results[0].plot()

                        for result in results:
                            if result.boxes is not None:
                                for box in result.boxes:
                                    cls = int(box.cls[0])
                                    class_name = (
                                        model.names[cls]
                                        if hasattr(model, "names")
                                        else str(cls)
                                    )
                                    audio_alerter.queue_alert(class_name)

                with slot_lock:
                    frame_slot[0] = annotated_frame

            frame_count += 1

    finally:
        cap.release()
        print(f"[{stream_label}] Camera released.")


def main():
    print("Setting up NCNN models...")
    setup_models()

    print(
        f"\nStarting two-window debug viewer: main cam={CAM_MAIN_INDEX}, tws cam={CAM_TWS_INDEX}"
    )
    print("Press 'q' in any window to quit.")

    stop_event = threading.Event()

    main_slot = [None]
    tws_slot = [None]
    main_lock = threading.Lock()
    tws_lock = threading.Lock()

    t_main = threading.Thread(
        target=run_stream_windowed,
        args=(
            CAM_MAIN_INDEX,
            ["road", "animal", "overtake"],
            True,
            "cam-main",
            stop_event,
            main_slot,
            main_lock,
        ),
        name="cam-main",
    )
    t_tws = threading.Thread(
        target=run_stream_windowed,
        args=(
            CAM_TWS_INDEX,
            ["tws"],
            False,
            "cam-tws",
            stop_event,
            tws_slot,
            tws_lock,
        ),
        name="cam-tws",
    )
    t_main.start()
    t_tws.start()

    try:
        while not stop_event.is_set():
            with main_lock:
                main_frame = main_slot[0]
            if main_frame is not None:
                cv2.imshow("Main Detections", main_frame)

            with tws_lock:
                tws_frame = tws_slot[0]
            if tws_frame is not None:
                cv2.imshow("TWS Detections", tws_frame)

            if cv2.waitKey(1) & 0xFF == ord("q"):
                stop_event.set()
                break

            if not t_main.is_alive() and not t_tws.is_alive():
                break

            time.sleep(0.01)
    finally:
        stop_event.set()
        t_main.join(timeout=5)
        t_tws.join(timeout=5)
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
