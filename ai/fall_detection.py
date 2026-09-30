"""Webcam + MediaPipe Pose fall detection.

Every frame is analysed in memory and thrown away. Only pose landmarks (numbers) are kept,
plus the single latest preview image for the live view (a skeleton on black in Privacy Mode).
This is a prototype, not a medically certified fall detector.
"""
import math
import os
import threading
import time

import cv2
import mediapipe as mp
import numpy as np

# ---- Tunable thresholds ------------------------------------------------------------
CAMERA_INDEX = 0
MIN_VISIBILITY = 0.5      # ignore landmarks MediaPipe is not confident about
UPRIGHT_ANGLE = 35        # torso within 35 degrees of vertical = upright
HORIZONTAL_ANGLE = 60     # torso more than 60 degrees from vertical = horizontal / low
FALL_WINDOW = 3.0         # seconds: the person must have been upright this recently
FALL_HOLD = 1.0           # seconds the body must stay horizontal before we call it a fall
# ------------------------------------------------------------------------------------

mp_pose = mp.solutions.pose
mp_draw = mp.solutions.drawing_utils
L_SHOULDER, R_SHOULDER, L_HIP, R_HIP = 11, 12, 23, 24


def _placeholder(text):
    img = np.zeros((360, 640, 3), np.uint8)
    cv2.putText(img, text, (40, 190), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (200, 200, 200), 2)
    return cv2.imencode(".jpg", img)[1].tobytes()


class FallDetector:
    def __init__(self):
        self.active = False            # monitoring on/off
        self.privacy_mode = True       # True = skeleton on black, False = camera image + skeleton
        self.camera_ok = False
        self.person_visible = False
        self.posture = "unknown"       # upright | leaning | horizontal | unknown
        self.torso_angle = None
        self.landmarks = {}            # {index: (x, y)} of confident landmarks
        self.jpeg = _placeholder("Starting camera...")
        self._fall_flag = False
        self._armed = True
        self._last_upright = 0.0
        self._horizontal_since = None
        self._thread = None

    # ---- public API ----------------------------------------------------------------
    def start(self):
        self.active = True
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

    def stop(self):
        self.active = False

    def pop_fall(self):
        """Return True once per detected fall."""
        fired, self._fall_flag = self._fall_flag, False
        return fired

    def snapshot(self):
        return {"camera_ok": self.camera_ok, "person_visible": self.person_visible,
                "posture": self.posture, "torso_angle": self.torso_angle}

    # ---- detection logic (pure, easy to test) -----------------------------------------
    @staticmethod
    def torso_angle_of(lms, w, h):
        """Angle of the shoulder-to-hip line from vertical: 0 = upright, 90 = lying flat."""
        pts = [lms[i] for i in (L_SHOULDER, R_SHOULDER, L_HIP, R_HIP)]
        if any(p.visibility < MIN_VISIBILITY for p in pts):
            return None                # hips not in view (e.g. sitting close to the laptop)
        sx = (lms[L_SHOULDER].x + lms[R_SHOULDER].x) / 2 * w
        sy = (lms[L_SHOULDER].y + lms[R_SHOULDER].y) / 2 * h
        hx = (lms[L_HIP].x + lms[R_HIP].x) / 2 * w
        hy = (lms[L_HIP].y + lms[R_HIP].y) / 2 * h
        return math.degrees(math.atan2(abs(sx - hx), abs(sy - hy)))

    def update(self, lms, w, h, now):
        """lms: MediaPipe landmark list or None. Updates posture and raises the fall flag."""
        if lms is None:
            self.person_visible, self.landmarks = False, {}
            self.posture, self.torso_angle, self._horizontal_since = "unknown", None, None
            return
        self.person_visible = True
        self.landmarks = {i: (p.x, p.y) for i, p in enumerate(lms) if p.visibility >= MIN_VISIBILITY}
        angle = self.torso_angle_of(lms, w, h)
        self.torso_angle = None if angle is None else round(angle)
        if angle is None:
            self.posture, self._horizontal_since = "unknown", None
        elif angle <= UPRIGHT_ANGLE:
            self.posture, self._horizontal_since = "upright", None
            self._last_upright, self._armed = now, True
        elif angle >= HORIZONTAL_ANGLE:
            self.posture = "horizontal"
            if self._horizontal_since is None:
                self._horizontal_since = now
            held = now - self._horizontal_since >= FALL_HOLD
            sudden = self._horizontal_since - self._last_upright <= FALL_WINDOW   # was upright moments ago
            if held and sudden and self._armed:
                self._fall_flag, self._armed = True, False
        else:
            self.posture, self._horizontal_since = "leaning", None

    # ---- camera loop -----------------------------------------------------------------
    def _open_camera(self):
        backend = cv2.CAP_DSHOW if os.name == "nt" else cv2.CAP_ANY
        cap = cv2.VideoCapture(CAMERA_INDEX, backend)
        if not cap.isOpened():
            cap.release()
            return None
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        return cap

    def _render(self, frame, result):
        canvas = np.zeros_like(frame) if self.privacy_mode else frame
        if result.pose_landmarks:
            mp_draw.draw_landmarks(
                canvas, result.pose_landmarks, mp_pose.POSE_CONNECTIONS,
                mp_draw.DrawingSpec(color=(120, 230, 160), thickness=3, circle_radius=3),
                mp_draw.DrawingSpec(color=(230, 230, 230), thickness=2))
        ok, buf = cv2.imencode(".jpg", canvas, [cv2.IMWRITE_JPEG_QUALITY, 70])
        if ok:
            self.jpeg = buf.tobytes()

    def _run(self):
        # model_complexity=1 ships inside the mediapipe package, so it works offline (0 downloads a model on first run).
        try:
            pose = mp_pose.Pose(model_complexity=1, min_detection_confidence=0.5, min_tracking_confidence=0.5)
        except Exception as exc:                             # keep the app alive; demo buttons still work
            print("Pose model failed to load:", exc)
            self.jpeg = _placeholder("Pose model failed to load")
            return
        cap = None
        while True:
            if not self.active:
                if cap is not None:
                    cap.release()
                    cap = None
                self.camera_ok = False
                self.update(None, 1, 1, time.time())
                self.jpeg = _placeholder("Monitoring paused")
                time.sleep(0.3)
                continue
            if cap is None:
                cap = self._open_camera()
                if cap is None:
                    self.camera_ok = False
                    self.update(None, 1, 1, time.time())
                    self.jpeg = _placeholder("Camera not available")
                    time.sleep(2)
                    continue
            ok, frame = cap.read()
            if not ok:
                cap.release()
                cap = None
                continue
            self.camera_ok = True
            frame = cv2.flip(frame, 1)                       # mirror view is more natural
            h, w = frame.shape[:2]
            result = pose.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            lms = result.pose_landmarks.landmark if result.pose_landmarks else None
            self.update(lms, w, h, time.time())
            self._render(frame, result)
            del frame                                        # raw frame is never kept
