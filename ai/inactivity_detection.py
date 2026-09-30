"""Prolonged-inactivity detection from pose landmarks (no images involved)."""
import math

MOVE_THRESHOLD = 0.03   # mean landmark shift (fraction of frame size) that counts as "meaningful movement"


class InactivityDetector:
    def __init__(self, threshold_seconds=30, move_threshold=MOVE_THRESHOLD):
        self.threshold = threshold_seconds       # demo-friendly; change via INACTIVITY_SECONDS in app.py
        self.move_threshold = move_threshold
        self.last_movement = None                # epoch seconds
        self.person_visible = False
        self._ref = None                         # landmarks at the last detected movement
        self._triggered = False

    def update(self, landmarks, now):
        """landmarks: {index: (x, y)} in 0-1 coordinates. Empty dict = nobody in view."""
        if not landmarks:
            self.person_visible = False
            self._ref = None                     # someone re-entering the frame counts as movement
            return
        self.person_visible = True
        if self._ref is None:
            self._ref = landmarks
            self._mark_movement(now)
            return
        common = [i for i in landmarks if i in self._ref]
        if not common:
            return
        shift = sum(math.dist(landmarks[i], self._ref[i]) for i in common) / len(common)
        if shift >= self.move_threshold:
            self._ref = landmarks
            self._mark_movement(now)

    def _mark_movement(self, now):
        self.last_movement = now
        self._triggered = False

    def reset(self, now):
        """Restart the inactivity clock (e.g. after the patient answers 'I'm OK')."""
        self._mark_movement(now)

    def idle_seconds(self, now):
        return None if self.last_movement is None else max(0.0, now - self.last_movement)

    def should_trigger(self, now):
        """True exactly once per stretch of inactivity."""
        idle = self.idle_seconds(now)
        if self.person_visible and not self._triggered and idle is not None and idle >= self.threshold:
            self._triggered = True
            return True
        return False
