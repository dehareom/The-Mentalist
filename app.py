"""ElderGuard AI - Flask app: pages, JSON API and the emergency state machine.

Run:  python app.py      then open http://127.0.0.1:5000
"""
import os
import threading
import time

from flask import Flask, Response, jsonify, redirect, render_template, request

from ai.fall_detection import FallDetector
from ai.inactivity_detection import InactivityDetector
from database import database as db

# ------------------------------------------------------------------------------------
# Configuration - edit here, or override with environment variables,
# e.g.  INACTIVITY_SECONDS=15 python app.py
# ------------------------------------------------------------------------------------
PATIENT_NAME = os.getenv("PATIENT_NAME", "Rajesh Kumar")
VERIFICATION_SECONDS = int(os.getenv("VERIFICATION_SECONDS", 10))            # patient answers "Are you okay?"
INACTIVITY_SECONDS = int(os.getenv("INACTIVITY_SECONDS", 30))                # demo value
CAREGIVER_TIMEOUT_SECONDS = int(os.getenv("CAREGIVER_TIMEOUT_SECONDS", 30))  # before escalating to level 2
COOLDOWN_SECONDS = int(os.getenv("COOLDOWN_SECONDS", 10))                    # quiet time after an alert closes
AUTO_START_MONITORING = os.getenv("AUTO_START_MONITORING", "1") == "1"
CAREGIVERS = {1: "Primary Caregiver", 2: "Secondary Caregiver"}

ALERT_TYPES = {
    "FALL":       {"headline": "Possible Fall Detected",        "severity": "HIGH",   "patient_text": "Possible fall detected."},
    "VOICE_SOS":  {"headline": "Voice SOS Detected",            "severity": "HIGH",   "patient_text": "We heard a call for help."},
    "INACTIVITY": {"headline": "Prolonged Inactivity Detected", "severity": "MEDIUM", "patient_text": "We have not seen you move for a while."},
    "MANUAL_SOS": {"headline": "Emergency Button Pressed",      "severity": "HIGH",   "patient_text": "You pressed the emergency button."},
}

app = Flask(__name__)
detector = FallDetector()
inactivity = InactivityDetector(threshold_seconds=INACTIVITY_SECONDS)


# ------------------------------------------------------------------------------------
# Emergency engine: one open alert at a time.
#   VERIFICATION (patient has N seconds)  -> ACTIVE (level 1 caregiver alerted)
#   ACTIVE (no acknowledgement in time)   -> ESCALATED (level 2 caregiver alerted)
#   patient "I'm OK"                      -> CANCELLED
#   caregiver acknowledges                -> ACKNOWLEDGED (resolved)
# ------------------------------------------------------------------------------------
class EmergencyEngine:
    def __init__(self):
        self.lock = threading.RLock()
        self.alert = None
        self.notice = None            # short message shown on the patient screen after an alert closes
        self.cooldown_until = 0.0

    def trigger(self, kind, message="", skip_verification=False, automatic=False):
        with self.lock:
            now = time.time()
            if self.alert or (automatic and now < self.cooldown_until):
                return None
            info = ALERT_TYPES[kind]
            status = "ACTIVE" if skip_verification else "VERIFICATION"
            text = message or info["headline"]
            alert_id = db.create_alert(kind, info["severity"], status, text)
            self.alert = {"id": alert_id, "type": kind, "status": status, "level": 0, "message": text,
                          "timestamp": db.now_str(), "verify_deadline": now + VERIFICATION_SECONDS,
                          "ack_deadline": None}
            if skip_verification:
                self._alert_caregiver(now)
            return self.alert

    def _current(self, alert_id):
        return self.alert if self.alert and self.alert["id"] == alert_id else None

    def _alert_caregiver(self, now):
        a = self.alert
        a.update(status="ACTIVE", level=1, ack_deadline=now + CAREGIVER_TIMEOUT_SECONDS)
        db.update_alert(a["id"], status="ACTIVE")

    def _close(self, text, kind):
        now = time.time()
        self.alert = None
        self.cooldown_until = now + COOLDOWN_SECONDS
        self.notice = {"text": text, "kind": kind, "until": now + 8}
        inactivity.reset(now)

    def tick(self):
        """Called several times a second: moves alerts forward when a timer runs out."""
        with self.lock:
            a, now = self.alert, time.time()
            if not a:
                return
            if a["status"] == "VERIFICATION" and now >= a["verify_deadline"]:
                self._alert_caregiver(now)
            elif a["status"] == "ACTIVE" and now >= a["ack_deadline"]:
                a.update(status="ESCALATED", level=2, ack_deadline=None)
                db.update_alert(a["id"], status="ESCALATED")

    def need_help(self, alert_id):
        with self.lock:
            a = self._current(alert_id)
            if a and a["status"] == "VERIFICATION":
                self._alert_caregiver(time.time())
                return True
            return False

    def cancel(self, alert_id):
        """Patient says 'I'm OK'."""
        with self.lock:
            if not self._current(alert_id):
                return False
            db.update_alert(alert_id, status="CANCELLED", resolved_at=db.now_str(),
                            resolution="Patient confirmed safe")
            self._close("🟢 Emergency cancelled. Patient confirmed safe.", "ok")
            return True

    def acknowledge(self, alert_id):
        """Caregiver acknowledges (works for level 1 and level 2)."""
        with self.lock:
            a = self._current(alert_id)
            if not a or a["status"] not in ("ACTIVE", "ESCALATED"):
                return False
            who, stamp = CAREGIVERS[a["level"]], db.now_str()
            db.update_alert(alert_id, status="ACKNOWLEDGED", acknowledged_at=stamp, resolved_at=stamp,
                            resolution=f"Acknowledged by {who}")
            self._close("🔵 Your caregiver has acknowledged the alert.", "info")
            return True

    def public(self):
        """Alert + notice in the shape the front-end needs."""
        with self.lock:
            now, a = time.time(), self.alert
            if self.notice and now > self.notice["until"]:
                self.notice = None
            notice = self.notice and {"text": self.notice["text"], "kind": self.notice["kind"]}
            if not a:
                return None, notice
            info = ALERT_TYPES[a["type"]]
            deadline = a["verify_deadline"] if a["status"] == "VERIFICATION" else a["ack_deadline"]
            return {"id": a["id"], "type": a["type"], "status": a["status"], "level": a["level"],
                    "headline": info["headline"], "patient_text": info["patient_text"],
                    "message": a["message"], "timestamp": a["timestamp"],
                    "seconds_left": None if deadline is None else max(0.0, deadline - now)}, notice


engine = EmergencyEngine()
_monitor = {"since": None}


def start_monitoring():
    if not detector.active:
        detector.start()
        _monitor["since"] = time.time()
        db.log_event("MONITORING_STARTED")


def stop_monitoring():
    if detector.active:
        detector.stop()
        db.log_event("MONITORING_STOPPED", duration=round(time.time() - (_monitor["since"] or time.time())))


def monitor_loop():
    """Background thread: turns detector output into alerts and advances alert timers."""
    while True:
        now = time.time()
        if detector.active:
            if detector.pop_fall():
                engine.trigger("FALL", automatic=True)
            inactivity.update(detector.landmarks, now)
            if inactivity.should_trigger(now):
                if not engine.trigger("INACTIVITY", f"No movement for {INACTIVITY_SECONDS} seconds", automatic=True):
                    inactivity.reset(now)      # busy or cooling down: count again from now
        engine.tick()
        time.sleep(0.2)


def activity_label(snap, idle):
    if not detector.active:
        return "Monitoring paused"
    if not snap["camera_ok"]:
        return "Camera unavailable"
    if not snap["person_visible"]:
        return "No person in view"
    if idle is None or idle < 10:
        return "Normal"
    return "Resting" if idle < INACTIVITY_SECONDS else "No movement"


# ------------------------------------------------------------------------------------
# Pages
# ------------------------------------------------------------------------------------
@app.get("/")
def index():
    return redirect("/patient")


@app.get("/patient")
def patient_page():
    return render_template("patient.html")


@app.get("/caregiver")
def caregiver_page():
    return render_template("caregiver.html")


@app.get("/history")
def history_page():
    return render_template("history.html")


# ------------------------------------------------------------------------------------
# API
# ------------------------------------------------------------------------------------
@app.get("/api/status")
def api_status():
    now = time.time()
    snap, (alert, notice) = detector.snapshot(), engine.public()
    idle = inactivity.idle_seconds(now)
    if alert is None:
        status = "SAFE"
    elif alert["status"] == "VERIFICATION":
        status = "WARNING"
    else:
        status = "EMERGENCY"
    return jsonify(patient=PATIENT_NAME, status=status, activity=activity_label(snap, idle),
                   last_movement_ago=idle, monitoring=detector.active, camera_ok=snap["camera_ok"],
                   posture=snap["posture"], privacy_mode=detector.privacy_mode,
                   alert=alert, notice=notice,
                   config={"verification_seconds": VERIFICATION_SECONDS,
                           "inactivity_seconds": INACTIVITY_SECONDS,
                           "caregiver_timeout_seconds": CAREGIVER_TIMEOUT_SECONDS})


@app.get("/api/events")
def api_events():
    return jsonify(db.get_history(request.args.get("limit", 50, type=int)))


@app.get("/api/alerts")
def api_alerts():
    return jsonify(db.get_alerts(request.args.get("limit", 50, type=int)))


@app.post("/api/alert")
def api_create_alert():
    """Used by the emergency button, voice SOS and the demo buttons."""
    data = request.get_json(silent=True) or {}
    kind = data.get("type")
    if kind not in ALERT_TYPES:
        return jsonify(error="Unknown alert type"), 400
    alert = engine.trigger(kind, str(data.get("message", ""))[:120], skip_verification=(kind == "MANUAL_SOS"))
    if not alert:
        return jsonify(error="Another alert is already open"), 409
    return jsonify(id=alert["id"], status=alert["status"]), 201


def _result(ok):
    return (jsonify(ok=True), 200) if ok else (jsonify(ok=False, error="No matching open alert"), 409)


@app.post("/api/alert/<int:alert_id>/acknowledge")
def api_acknowledge(alert_id):
    return _result(engine.acknowledge(alert_id))


@app.post("/api/alert/<int:alert_id>/cancel")
def api_cancel(alert_id):
    return _result(engine.cancel(alert_id))


@app.post("/api/alert/<int:alert_id>/escalate")
def api_escalate(alert_id):
    """Patient pressed 'Need Help' during verification: alert the caregiver immediately."""
    return _result(engine.need_help(alert_id))


@app.post("/api/monitoring/start")
def api_monitoring_start():
    start_monitoring()
    return jsonify(monitoring=detector.active)


@app.post("/api/monitoring/stop")
def api_monitoring_stop():
    stop_monitoring()
    return jsonify(monitoring=detector.active)


@app.post("/api/privacy")
def api_privacy():
    detector.privacy_mode = bool((request.get_json(silent=True) or {}).get("enabled", True))
    return jsonify(privacy_mode=detector.privacy_mode)


def _mjpeg():
    while True:
        frame = detector.jpeg
        yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
        time.sleep(0.07)


@app.get("/video_feed")
def video_feed():
    """Live preview only - nothing is recorded. Skeleton-only while Privacy Mode is on."""
    return Response(_mjpeg(), mimetype="multipart/x-mixed-replace; boundary=frame")


if __name__ == "__main__":
    db.init_db()
    if AUTO_START_MONITORING:
        start_monitoring()
    threading.Thread(target=monitor_loop, daemon=True).start()
    print("ElderGuard running: http://127.0.0.1:5000/patient  and  http://127.0.0.1:5000/caregiver")
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
