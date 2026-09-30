# ElderGuard AI
**Detect. Verify. Escalate. Protect.**

A hackathon prototype for privacy-conscious elderly safety monitoring using only a laptop webcam and microphone.
It is **not** a medical device: no medical-grade accuracy, no guaranteed fall detection, no guaranteed emergency response.

> Video is processed locally for real-time detection and continuous footage is not stored.

## Run it
Needs Python 3.9 to 3.12 (MediaPipe does not support 3.13 yet) and Chrome or Edge.

```bash
python -m venv venv
venv\Scripts\activate          # macOS/Linux: source venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open two browser windows side by side:
- Patient: http://127.0.0.1:5000/patient (allow the microphone when asked)
- Caregiver: http://127.0.0.1:5000/caregiver
- History: http://127.0.0.1:5000/history

## 2-3 minute demo
1. **Normal**: patient page says "YOU ARE SAFE"; caregiver page shows SAFE and Monitoring ACTIVE.
2. **Fall**: stand 2-3 m back so your shoulders and hips are visible, then drop to the floor (or lie down fast). "Are you okay?" appears with a 10 s countdown.
3. **Patient confirms**: press **I'M OK**. Green "Emergency cancelled" notice; the event lands in History.
4. **Voice SOS**: say "help me". Verification starts. Say it again (or press NEED HELP) to alert the caregiver at once.
5. **Escalation**: let a countdown run out. Caregiver page turns red, Level 1 waits 30 s, then Level 2 lights up. Press **ACKNOWLEDGE ALERT** to resolve.
6. **Inactivity**: stay still in view for 30 s. Same "Are you okay?" flow.

If the camera or mic misbehaves, the caregiver page has **Demo tools** buttons that trigger each alert type.

## Configuration
Edit the block at the top of `app.py`, or set environment variables (`INACTIVITY_SECONDS=15 python app.py`).

| Setting | Default | Meaning |
|---|---|---|
| `VERIFICATION_SECONDS` | 10 | Time the patient has to answer "Are you okay?" |
| `INACTIVITY_SECONDS` | 30 | No-movement time before asking (demo value) |
| `CAREGIVER_TIMEOUT_SECONDS` | 30 | Time before escalating to Level 2 |
| `COOLDOWN_SECONDS` | 10 | Quiet time after an alert closes (automatic detections only) |
| `PATIENT_NAME` | Rajesh Kumar | Shown on dashboards |

Detection thresholds live at the top of `ai/fall_detection.py` (`UPRIGHT_ANGLE`, `HORIZONTAL_ANGLE`, `FALL_WINDOW`, `FALL_HOLD`) and `ai/inactivity_detection.py` (`MOVE_THRESHOLD`).

## How detection works
- **Fall**: MediaPipe Pose gives shoulder and hip landmarks. The torso angle from vertical is tracked. A fall is flagged when the torso goes from upright (under 35 degrees) to horizontal (over 60 degrees) within 3 s and stays there for 1 s. Someone who never stood up (already lying down) does not trigger it.
- **Inactivity**: average landmark movement is compared with the last "meaningful movement". No movement for the threshold asks "Are you okay?" first; it never alerts the caregiver directly.
- **Voice**: browser Web Speech API listens for "help me", "emergency", "i need help", "i fell down". Only the matched phrase is stored, never a transcript.

## Privacy
- Frames are analysed in memory and discarded; nothing is written to disk. Only the latest preview image is held for the live view.
- **Privacy mode** (on by default) shows only a skeleton on black, not the camera image.
- Caveat: in Chrome and Edge the Web Speech API sends microphone audio to the browser vendor's speech service. Camera video never leaves the laptop, but voice recognition is not fully local.

## Known limits
- Bending down, sitting on the floor or lying on a sofa in view can look like a fall. Hips hidden behind a desk means no fall detection (movement and inactivity still work).
- Use good light and a full-body view. One person in frame.
- Caregiver escalation is shown in the dashboard only; no SMS or calls.

## Files
`app.py` (Flask, API, emergency engine) | `ai/` (fall + inactivity) | `database/database.py` (SQLite, creates `elderguard.db`) | `templates/` (patient, caregiver, history) | `static/` (style.css, script.js)

API: `GET /api/status`, `/api/events`, `/api/alerts`; `POST /api/alert`, `/api/alert/<id>/acknowledge|cancel|escalate`, `/api/monitoring/start|stop`, `/api/privacy`.
