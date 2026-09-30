/* ElderGuard front-end: one file for the patient, caregiver and history pages. */
const $ = (id) => document.getElementById(id);
const page = document.body.dataset.page;
const show = (id, on) => { $(id).hidden = !on; };
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
let state = null;   // latest /api/status

async function api(url, method = "GET", body) {
  const res = await fetch(url, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  return res.json().catch(() => ({}));
}

function ago(sec) {
  if (sec == null) return "Not seen yet";
  if (sec < 10) return "Just now";
  if (sec < 60) return `${Math.round(sec)} seconds ago`;
  const m = Math.floor(sec / 60);
  return `${m} minute${m > 1 ? "s" : ""} ago`;
}

function fmtTime(ts, withDate = false) {
  if (!ts) return "–";
  const d = new Date(ts.replace(" ", "T"));
  const t = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  return withDate ? `${d.toLocaleDateString([], { day: "numeric", month: "short" })}, ${t}` : t;
}

const TYPE_LABEL = { FALL: "Possible Fall", VOICE_SOS: "Voice SOS", INACTIVITY: "Prolonged Inactivity", MANUAL_SOS: "Manual SOS",
                     MONITORING_STARTED: "Monitoring started", MONITORING_STOPPED: "Monitoring stopped" };
const STATUS_LABEL = { VERIFICATION: "Verifying with patient", ACTIVE: "Caregiver alerted", ESCALATED: "Escalated to secondary caregiver",
                       ACKNOWLEDGED: "Caregiver acknowledged", CANCELLED: "Patient confirmed safe", RESOLVED: "Resolved", INFO: "" };
const DOT = { HIGH: "red", MEDIUM: "amber", INFO: "blue" };
const statusText = (e) => e.resolution || STATUS_LABEL[e.status] || e.status || "";

/* ---------- polling ---------- */
async function poll() {
  try {
    state = await api("/api/status");
    (page === "patient" ? renderPatient : renderCaregiver)(state);
  } catch (_) { /* server restarting - try again on the next tick */ }
}
const act = (url, method = "POST", body) => api(url, method, body).then(poll);

/* ---------- patient page ---------- */
function renderPatient(s) {
  document.body.className = `patient state-${s.status.toLowerCase()}`;
  const a = s.alert, verifying = !!a && a.status === "VERIFICATION";
  show("view-safe", !a); show("view-help", !a);
  show("view-verify", verifying); show("view-active", !!a && !verifying);
  $("p-activity").textContent = s.activity;
  $("p-last").textContent = ago(s.last_movement_ago);
  if (verifying) {
    $("v-text").textContent = a.patient_text;
    $("v-count").textContent = Math.ceil(a.seconds_left);
  } else if (a) {
    $("a-text").textContent = a.level === 2
      ? "Your first contact has not answered yet. We are now alerting your second contact."
      : "Your caregiver has been alerted and can see your alert.";
  }
  show("notice", !!s.notice);
  if (s.notice) { $("notice").textContent = s.notice.text; $("notice").className = `notice ${s.notice.kind}`; }
}

function wirePatient() {
  $("btn-emergency").onclick = () => act("/api/alert", "POST", { type: "MANUAL_SOS" });
  $("btn-ok").onclick = $("btn-ok2").onclick = () => state?.alert && act(`/api/alert/${state.alert.id}/cancel`);
  $("btn-help").onclick = () => state?.alert && act(`/api/alert/${state.alert.id}/escalate`);
}

/* ---------- voice SOS (browser Web Speech API) ---------- */
const KEYWORDS = ["help me", "emergency", "i need help", "i fell down"];
let rec = null, voiceWanted = true, lastVoiceHit = 0;

function setVoice(text, on) { const b = $("voice-toggle"); b.textContent = text; b.classList.toggle("on", on); }

function onVoiceSOS(phrase) {
  if (Date.now() - lastVoiceHit < 5000) return;      // interim results repeat; react once
  lastVoiceHit = Date.now();
  const a = state?.alert;
  if (a && a.status === "VERIFICATION") act(`/api/alert/${a.id}/escalate`);     // "help me" during the countdown = Need Help
  else if (!a) act("/api/alert", "POST", { type: "VOICE_SOS", message: `Heard: "${phrase}"` });   // only the phrase is kept
}

function startVoice() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) { setVoice("🎤 Voice help is not supported in this browser. Use the button.", false); $("voice-toggle").disabled = true; return; }
  rec = new SR();
  rec.continuous = true; rec.interimResults = true; rec.lang = "en-IN";
  rec.onstart = () => setVoice("🎤 Voice help is on. Say “help me”.", true);
  rec.onresult = (e) => {
    const heard = Array.from(e.results).slice(e.resultIndex).map((r) => r[0].transcript).join(" ").toLowerCase();
    const hit = KEYWORDS.find((k) => heard.includes(k));
    if (hit) onVoiceSOS(hit);
  };
  rec.onerror = (e) => {
    if (e.error === "not-allowed" || e.error === "service-not-allowed") { voiceWanted = false; setVoice("🎤 Microphone blocked. Tap to try again.", false); }
  };
  rec.onend = () => { if (voiceWanted) setTimeout(() => { try { rec.start(); } catch (_) {} }, 300); };
  try { rec.start(); } catch (_) {}
  $("voice-toggle").onclick = () => {
    voiceWanted = !voiceWanted;
    if (voiceWanted) { try { rec.start(); } catch (_) {} }
    else { rec.stop(); setVoice("🎤 Voice help is off. Tap to turn on.", false); }
  };
}

/* ---------- caregiver page ---------- */
function renderCaregiver(s) {
  const banner = { SAFE: ["s-safe", "🟢 SAFE"], WARNING: ["s-warn", "🟡 CHECKING ON PATIENT"], EMERGENCY: ["s-danger", "🔴 EMERGENCY"] }[s.status];
  $("c-status-card").className = `card status-card ${banner[0]}`;
  $("c-status").textContent = banner[1];
  $("c-patient").textContent = s.patient;
  $("c-activity").textContent = s.activity;
  $("c-last").textContent = ago(s.last_movement_ago);
  $("c-monitoring").textContent = s.monitoring ? "🟢 ACTIVE" : "⚪ PAUSED";
  $("btn-monitor").textContent = s.monitoring ? "Pause monitoring" : "Start monitoring";
  $("c-posture").textContent = s.posture;
  if (document.activeElement !== $("privacy-toggle")) $("privacy-toggle").checked = s.privacy_mode;

  const a = s.alert;
  show("c-alert", !!a);
  if (!a) return;
  const verifying = a.status === "VERIFICATION", escalated = a.status === "ESCALATED";
  $("c-alert").className = `card alert-card ${verifying ? "verifying" : "emergency"}`;
  $("c-alert-title").textContent = verifying ? "🟡 CHECKING ON PATIENT" : "🔴 EMERGENCY ALERT";
  $("c-alert-what").textContent = a.headline;
  $("c-alert-time").textContent = `Time: ${fmtTime(a.timestamp)}`;
  $("c-alert-note").textContent = verifying ? `Waiting for the patient to answer (${Math.ceil(a.seconds_left)}s). You will be alerted if there is no reply.` : "";
  show("c-ladder", !verifying); show("btn-ack", !verifying);
  if (!verifying) {
    $("lvl1").className = `step ${escalated ? "done" : "now"}`;
    $("lvl1-note").textContent = escalated ? "No acknowledgement" : `Waiting for acknowledgement (${Math.ceil(a.seconds_left)}s)`;
    $("lvl2").className = `step ${escalated ? "now" : "later"}`;
    $("lvl2-note").textContent = escalated ? "Alert escalated, waiting for acknowledgement" : "Will be alerted if no one responds";
  }
}

function wireCaregiver() {
  $("btn-ack").onclick = () => state?.alert && act(`/api/alert/${state.alert.id}/acknowledge`);
  $("btn-monitor").onclick = () => act(state?.monitoring ? "/api/monitoring/stop" : "/api/monitoring/start");
  $("privacy-toggle").onchange = (e) => api("/api/privacy", "POST", { enabled: e.target.checked });
  document.querySelectorAll("[data-sim]").forEach((b) => {
    b.onclick = () => act("/api/alert", "POST", { type: b.dataset.sim, message: "Demo trigger" });
  });
}

/* ---------- event lists (caregiver sidebar + history page) ---------- */
async function loadEvents() {
  const events = await api(`/api/events?limit=${page === "history" ? 100 : 6}`);
  if (!Array.isArray(events)) return;
  if (page === "history") {
    $("h-rows").innerHTML = events.length ? events.map((e) => `
      <tr>
        <td>${esc(fmtTime(e.timestamp, true))}</td>
        <td><b>${esc(TYPE_LABEL[e.type] || e.type)}</b></td>
        <td><span class="sev ${esc(e.severity)}">${esc(e.severity)}</span></td>
        <td>${esc(STATUS_LABEL[e.status] ?? e.status ?? "")}</td>
        <td>${esc(e.resolution || "–")}</td>
        <td>${esc(e.acknowledged_at ? fmtTime(e.acknowledged_at) : "–")}</td>
      </tr>`).join("") : `<tr><td colspan="6" class="muted">No events yet.</td></tr>`;
  } else {
    $("c-events").innerHTML = events.length ? events.map((e) => `
      <li><span class="dot ${DOT[e.severity] || "blue"}"></span>
        <div><b>${esc(fmtTime(e.timestamp))} ${esc(TYPE_LABEL[e.type] || e.type)}</b>
        <div class="sub">${esc(statusText(e))}</div></div></li>`).join("") : `<li class="muted">No events yet.</li>`;
  }
}

/* ---------- start ---------- */
if (page === "patient") { wirePatient(); startVoice(); }
if (page === "caregiver") wireCaregiver();
if (page === "history") { loadEvents(); setInterval(loadEvents, 3000); }
else { poll(); setInterval(poll, 500); }
if (page === "caregiver") { loadEvents(); setInterval(loadEvents, 3000); }
