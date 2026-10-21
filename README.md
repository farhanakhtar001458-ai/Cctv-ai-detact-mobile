# Smart Classroom AI — CCTV phone-use & occupancy monitor

A production-oriented, privacy-conscious monitoring system that connects to existing
CCTV cameras and answers three questions in real time:

1. **How many people are visible?** (anonymous tracking IDs, cap 50 per camera)
2. **Is a mobile phone visible?**
3. **Is a specific person *sustainedly* using that phone?** — with confidence,
   persistence thresholds, cooldowns, and an audible warning.

It is **not** a facial-recognition system. People are tracked as `Person-01`,
`Person-02`, … for a session only; no biometric profile is created, no video is
stored, and no audio is captured.

```
 ┌────────────┐   RTSP/ONVIF   ┌───────────────────────────────────────────────┐
 │ CCTV cams  ├───────────────►│ Camera worker (thread per camera)             │
 └────────────┘   file/webcam  │  1 detect people+phones → 2 track → 3 associate│
 ┌────────────┐                │  → 4 temporal evidence → 5 alert → 6 cooldown │
 │ Dashboard  │◄── WebSocket ──┤ FastAPI · SQLite/PostgreSQL · event log · TTS │
 └────────────┘   MJPEG/API    └───────────────────────────────────────────────┘
```

---

## Status of this build (read this first)

| Area | State |
|---|---|
| Backend API, auth, RBAC, audit log, rate limiting | **Working**, covered by tests |
| Detection pipeline (detect → track → associate → temporal states → alerts) | **Working**, 104 automated tests |
| Live dashboard (React + TypeScript), WebSocket updates, MJPEG previews | **Working** |
| Event log, CSV export, retention purge, manual review | **Working** |
| Voice alerts (browser TTS + optional server TTS) | **Working** (browser path needs one click to enable audio) |
| Simulated demo camera | **Working** — clearly watermarked `SIMULATED` and tagged `source=simulated` |
| Real YOLO phone detection | **Requires model weights** — see [Provisioning the detector](#3-provision-the-detector-model) |
| ONVIF media-profile resolution | Discovery works with no extra deps; RTSP resolution needs `onvif-zeep` |
| Benchmarks on *your* hardware | **Not claimed** — run `scripts/benchmark.py`; never assume 50-person real-time |

> **Honest-detection rule.** Events, alerts and exports always carry
> `source=model` (real inference) or `source=simulated` (demo feed). The UI shows a
> SIMULATED watermark and an amber banner. Nothing in this repository presents
> simulated data as an AI result.

If no YOLO weights are present, the backend falls back to an OpenCV HOG people
detector and says so loudly (`degraded=true`, `phone_capable=false`) in the
dashboard, `/api/system/info` and the health endpoint — it will never pretend to
detect phones it cannot see.

### What was actually executed while building this (2 vCPU, 4 GB RAM, no GPU)

Every row below was run end-to-end in a sandbox; the numbers are from that box,
not from a marketing sheet.

| Check | Command | Result |
|---|---|---|
| Test suite | `make test` | **104 passed** (unit + API + WebSocket + MJPEG + detector contract) |
| Environment doctor | `make check` | 0 failures, 7 warnings (no GPU, no YOLO weights, no server TTS) — exits 0 |
| Detector adapter with real weights file | `UltralyticsDetector` on a locally built `yolo11n.pt` | loads, reports classes, `phone_capable=false` for the COCO names it was given |
| Benchmark | `scripts/benchmark.py --source sim://classroom --frames 200` | HOG fallback: **16.6 FPS**, p50 **40.9 ms** at 640×360 |
| File-source camera | create `source_type=file` camera on a generated clip, start | worker ran the real (non-simulated) path: 7 people tracked, 46 ms/frame, low-light flag set |
| MJPEG preview | `GET /api/cameras/1/stream.mjpg?token=…` | `multipart/x-mixed-replace`, 25 frames in 4 s, annotated |
| Live dashboard | Vite dev server + built SPA on `/` | renders and streams against the same API |
| Model download | `scripts/fetch_model.py --check` | correctly reports missing weights, exit 1 |
| Evaluation | `scripts/evaluate.py` | fails fast with guidance when weights are absent; full report (sweep + confusion matrix + SVG) when a file is supplied |
| Dataset prep | `scripts/prepare_dataset.py --src … --out …` | writes `data.yaml` (`nc: 2`, `0: person`, `67: cell phone`) |

Not executed here (no Docker, no GPU, no internet access to model hosts): the
container build, CUDA benchmarking, ONVIF against physical cameras, and real
classroom accuracy numbers. Do those on your target hardware before trusting any
performance figure.

---

## 1. Features

**Detection (Python / OpenCV / PyTorch / Ultralytics / ONNX Runtime)**

* Person + mobile-phone detection with configurable confidence thresholds.
* Multi-object tracking with stable anonymous IDs (`Person-01…`), motion prediction,
  occlusion recovery (gap tolerance), and a hard cap of 50 visible people per camera.
* Multi-stage phone-use pipeline: bounding-box containment, proximity, upper-body
  "use zone", head zone, size plausibility, temporal continuity → **five explicit
  states**: `person detected`, `mobile phone detected`, `possible phone use`,
  `sustained phone-use alert`, `uncertain`.
* Ambiguous assignments (two nearby students, one phone) are reported as
  `uncertain` and never trigger an alert.
* Low-light detection, frame sampling, adjustable inference resolution, optional
  ONNX Runtime / TensorRT export.

**Alerting**

* Alerts only after a configurable persistence window (default 2 s sustained,
  0.65 use-confidence) — a single frame can never alert.
* Per-person cooldown (default 60 s), global announcement rate limit, duplicate
  suppression with an explicit "suppressed: cooldown" entry in the feed.
* Text-to-speech: `"Mobile phone detected. Please follow classroom rules."`
  via browser Web Speech API on the monitoring PC, or a server-side engine
  (`espeak-ng` / `say` / `pyttsx3`) for an authorised speaker system. Volume, rate,
  language and message are configurable; simulated feeds are prefixed `[SIMULATED]`.

**Cameras**

* RTSP / RTSPS / HTTP / MJPEG, local video files, USB webcams, simulated demo feed.
* ONVIF WS-Discovery probe (no extra dependency).
* Credentials are encrypted at rest (Fernet, key derived from `SCAI_SECRET_KEY`);
  the API only ever returns a sanitised URL, logs are redacted, and previews are
  proxied so the RTSP URL never reaches the browser.
* Per-camera: inference resolution, frame sampling, person/phone thresholds,
  persistence threshold, cooldown, enable/disable, auto-start.
* Reconnection with exponential backoff, per-camera status, and a worker cap.

**Dashboard (React 18 + TypeScript, Vite)**

* Dark industrial monitoring UI with live annotated previews (MJPEG), bounding
  boxes, person IDs, phone boxes, per-person state chips.
* KPIs: occupancy, active alerts, phones visible, 24 h alert counts; FPS and
  inference latency per camera.
* Colours **and** icons **and** text labels for every state (green person, blue
  phone, amber possible use, red sustained alert, grey uncertain).
* Event timeline, alert feed, voice status, sensitivity control, camera
  configuration, reports, audit log.

**Backend / operations**

* FastAPI + SQLAlchemy 2 (SQLite by default, PostgreSQL supported), WebSockets for
  realtime updates, JWT auth with `admin` / `supervisor` / `viewer` roles.
* Audit log, rate limiting on sensitive endpoints, strict input validation,
  security headers, credential redaction.
* Event retention policy (default 90 days) with hourly purge task; metrics kept 14 days.

---

## 2. Quick start (local, 5 minutes)

Requirements: **Python 3.10+**, **Node 18+** (only for the dashboard), ~2 GB RAM.

```bash
git clone <this-repo> && cd Cctv-ai-detact-mobile

# --- backend ---
python3 -m venv .venv
source .venv/bin/activate                     # Windows: .venv\Scripts\activate
pip install -r backend/requirements.txt
pip install -r backend/requirements-vision.txt   # PyTorch + Ultralytics (skip for API-only dev)
cp backend/.env.example backend/.env          # optional; all values have defaults

cd backend
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

On first start the server creates the database, an admin account, a supervisor
account and two cameras (a **simulated demo feed** and an empty RTSP example).

```
username: admin
password: <printed once in the log, or in backend/data/initial_credentials.txt>
```

Set a known password instead by exporting `SCAI_BOOTSTRAP_ADMIN_PASSWORD=...`
before the first start. **Change it after logging in.**

```bash
# --- dashboard (second terminal) ---
cd frontend
npm install
npm run dev          # http://localhost:5173  (proxies /api to :8000)
```

Open <http://localhost:5173>, sign in, and press **Start** on the demo camera.
Click **Enable audio** to allow spoken alerts in your browser.

Production-style single process (API serves the built dashboard):

```bash
cd frontend && npm run build && cd ..
cd backend && uvicorn app.main:app --host 0.0.0.0 --port 8000
# → http://localhost:8000
```

---

## 3. Provision the detector model

The pipeline uses COCO-pretrained YOLO11, whose classes include `person` (0) and
`cell phone` (67).

```bash
cd backend
python scripts/fetch_model.py             # downloads yolo11n.pt (needs internet)
python scripts/fetch_model.py --check     # verify the weights are real, not an LFS pointer
```

Air-gapped host:

```bash
# on a connected machine
pip install ultralytics && yolo export model=yolo11n.pt format=onnx
# copy files across, then
python scripts/fetch_model.py --source /media/usb/yolo11n.pt --sha256 <digest>
```

Configuration (`backend/.env`):

```ini
SCAI_MODEL_PATH=yolo11n.pt        # or an absolute path to your fine-tuned best.pt
SCAI_DETECTOR_BACKEND=auto        # auto | ultralytics | onnx | hog | none
SCAI_MODEL_DEVICE=auto            # auto | cpu | cuda:0 | mps
SCAI_MODEL_IMGSZ=640
SCAI_ONNX_MODEL_PATH=             # when SCAI_DETECTOR_BACKEND=onnx
SCAI_ALLOW_MODEL_AUTODOWNLOAD=true
```

> `yolo11s.pt` noticeably improves recall for **small phones** on wide CCTV views at
> roughly 2× the compute; benchmark before switching. Ultralytics YOLO is
> AGPL-3.0 (or commercially licensed) — verify the licence for your deployment.

Verify what the running system actually has:

```bash
curl -s localhost:8000/api/health
cd backend && python scripts/check_env.py
```

---

## 4. Connect your CCTV cameras

1. Sign in as `supervisor` or `admin`.
2. **Cameras → Add camera**.
3. Fill in name, location, source type (`RTSP`), stream URL
   (e.g. `rtsp://192.168.1.64:554/Streaming/Channels/101`), username and password.
4. Press **Test connection** (reports resolution + latency without saving).
5. Set inference resolution (e.g. `1280×720` to see faces of phones, `960×540` for
   throughput), frame sampling (`1/3` = analyse 1 of every 3 frames), confidence
   thresholds, persistence threshold and alert cooldown.
6. **Save**, then **Start**.

**ONVIF**: press *Discover ONVIF* to list devices answering WS-Discovery, then paste
that device's RTSP URL. For automatic profile resolution install `onvif-zeep`.

**Sample video mode** (no camera needed):

```bash
cd backend && python scripts/generate_demo_video.py --out samples/sim_classroom.mp4 --seconds 60
# Cameras → Add camera → Source type "Local video file" → path /abs/path/sim_classroom.mp4
```

Supported URL forms: `rtsp://`, `rtsps://`, `http(s)://` (MJPEG), a filesystem path,
a webcam index (`0`), or `sim://classroom`.

---

## 5. How phone-use detection works

| Stage | Module | What it decides |
|---|---|---|
| 1. Detect | `backend/app/vision/detector.py` | YOLO/ONNX/HOG detections split into people and phones with per-class thresholds |
| 2. Track | `backend/app/vision/tracker.py` | IoU + motion-prediction tracker (or BYTETracker) → stable `Person-NN`, gap recovery, 50-track cap |
| 3. Associate | `backend/app/vision/association.py` | Greedy one-to-one phone↔person matching from containment, proximity, use-zone, head-zone, size plausibility, continuity; ambiguity flagged |
| 4. Temporal evidence | `backend/app/vision/temporal.py` | Sliding window (default 4 s) per person → `use_score`, sustained duration, ambiguity ratio, hysteresis |
| 5. Alert | `backend/app/services/alerts.py` | `sustained_phone_use` only when score ≥ persistence **and** sustained ≥ 2 s; heavy occlusion downgrades to `uncertain` |
| 6. Suppress | `backend/app/vision/temporal.py::AlertSuppressor` | Per-person cooldown, per-camera rate limit, explicit `suppressed` events |

State meanings (shown identically in the UI, API and event log):

| State | Colour + label | Meaning |
|---|---|---|
| `none` | green · PERSON | Person visible, no phone evidence |
| `phone_detected` | blue · PHONE | Phone visible near the person, use not established |
| `possible_phone_use` | amber · POSSIBLE | Holding/looking evidence accumulating below threshold |
| `sustained_phone_use` | red · ALERT | Persistence + confidence satisfied → announcement allowed |
| `uncertain` | grey · UNCERTAIN | Ambiguous assignment or heavy occlusion → manual review |

The system never claims "confirmed" use: the API and CSV call the strong state
**"sustained phone-use alert"**, together with the confidence, duration and
reasoning string that produced it (`evidence` JSON on every event).

Tune sensitivity in **Monitor → Alert sensitivity** or **Settings → Detection**:

```ini
persistence_threshold       0.65   # higher = fewer, more certain alerts
min_sustained_seconds       2.0
analysis_window_seconds     4.0
alert_cooldown_seconds      60
phone_conf                  0.30
max_visible_people          50
```

---

## 6. Voice alerts

Default: **dashboard output**. The backend decides *when* to announce; the monitoring
browser speaks it. Browsers require a user gesture, so press **Enable audio** (or the
banner button) once per session.

Server output (authorised classroom speaker system): install `espeak-ng` (Linux) or
use macOS `say`, set `SCAI_VOICE_OUTPUT=server`, and route the monitoring host's
audio output to the speaker system. Clips are also served at
`/api/voice/clip/<file>?token=…` if you prefer an external audio router.

Configurable: enabled flag, output (`dashboard`/`server`/`both`), message per event
type, language tag, rate, volume, announcement cooldown, max announcements/minute.

> Audio is **never** sent to a camera or speaker automatically. Broadcasting to
> classroom hardware is an explicit deployment decision documented in
> [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md).

---

## 7. API overview

Full interactive docs at `/api/docs` (development only). All endpoints require
`Authorization: Bearer <token>` except `/api/health`.

| Method | Endpoint | Purpose |
|---|---|---|
| POST | `/api/auth/login`, `/api/auth/token` | JWT login (JSON / OAuth2 form) |
| GET | `/api/auth/me`, `/api/auth/permissions` | Identity + role capabilities |
| POST | `/api/auth/stream-token` | 10-min token for `<img>` MJPEG previews |
| GET/POST/PATCH/DELETE | `/api/cameras…` | Camera CRUD, `start`, `stop`, `restart`, `test`, `discover/onvif` |
| GET | `/api/cameras/{id}/snapshot.jpg`, `/stream.mjpg`, `/analysis`, `/status` | Previews + latest detections |
| GET | `/api/events` | Filter by camera, category, source, tracking id, time |
| POST | `/api/events/{id}/acknowledge` | Manual review (supervisor+) |
| GET | `/api/events/export/csv` | Authorised CSV export (logged) |
| DELETE | `/api/events/purge` | Apply retention policy now |
| GET | `/api/stats/overview`, `/daily`, `/cameras`, `/hourly`, `/live` | Dashboard figures |
| GET/PUT/POST | `/api/settings…` | Runtime config, voice test, model reload |
| GET | `/api/system/info`, `/api/system/health`, `/api/audit` | Diagnostics + audit trail |
| WS | `/api/ws?token=…` | `frame`, `event`, `alert`, `voice`, `camera` messages |

Example:

```bash
TOKEN=$(curl -s -X POST localhost:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"YOUR_PASSWORD"}' | jq -r .access_token)

curl -s localhost:8000/api/stats/overview -H "Authorization: Bearer $TOKEN"
curl -s "localhost:8000/api/events?category=sustained_phone_use&days=1" -H "Authorization: Bearer $TOKEN"
```

---

## 8. Testing

```bash
cd backend
pip install pytest httpx pytest-asyncio
pytest -q                     # 104 tests: geometry, tracker, association, temporal,
                              # pipeline, API/auth/RBAC, credentials, events, metrics,
                              # websocket + MJPEG preview, detector backends, evaluation
cd ../frontend
npm run typecheck && npm run build
```

What the tests prove (and what they do not):

* Tracker keeps IDs stable, survives occlusion, separates two overlapping people,
  and tracks 50 simultaneous targets.
* Phone→person association never assigns one phone to two people; ambiguous cases
  are flagged; implausible boxes are rejected.
* The temporal state machine never alerts on a single frame, restarts persistence
  after a gap, and downgrades ambiguous/occluded detections.
* Credentials never appear in API responses, are encrypted at rest, and are
  redacted from logs.
* RBAC (viewer/supervisor/admin) and rate limiting behave as documented.
* **Not** proven by the test suite: real-world accuracy. That requires your labelled
  footage — see [`docs/TRAINING.md`](docs/TRAINING.md) and
  [`docs/EVALUATION.md`](docs/EVALUATION.md).

---

## 9. Performance, training and deployment

```bash
cd backend
python scripts/generate_demo_video.py --out samples/sim_classroom.mp4 --seconds 60
python scripts/benchmark.py --source sim://classroom --frames 200 \
       --resolutions 640x360,960x540,1280x720 --people 10,25,50
python scripts/prepare_dataset.py --src datasets/raw --out datasets/classroom
python scripts/train.py --data datasets/classroom/data.yaml --model yolo11n.pt --epochs 80
python scripts/evaluate.py --data datasets/classroom --split test --conf 0.35
python scripts/export_onnx.py --model runs/classroom/yolo_classroom/weights/best.pt
```

* [Hardware benchmarking guide](docs/BENCHMARKING.md) — what to measure, how to size
  a host for N cameras, and how to verify the 50-person requirement.
* [Training & dataset guide](docs/TRAINING.md) — annotation rules, licensing,
  consent, class imbalance, hard negatives (books, calculators, empty hands).
* [Evaluation protocol](docs/EVALUATION.md) — precision/recall/AP, confusion matrix,
  alert-level precision, lighting/occlusion test matrix.
* [Deployment guide](docs/DEPLOYMENT.md) — Docker, systemd, Nginx TLS, PostgreSQL,
  multi-worker scaling, GPU notes, speaker-system routing.
* [Architecture](docs/ARCHITECTURE.md) · [API reference](docs/API.md) ·
  [Privacy & compliance](docs/PRIVACY.md) · [Operations runbook](docs/OPERATIONS.md).

Docker:

```bash
docker compose up --build        # http://localhost:8000
```

---

## 10. Security & privacy summary

* JWT (HS256) with role-based access: **viewer** (read), **supervisor** (cameras,
  review, export), **admin** (settings, users, audit).
* Camera passwords encrypted with a key derived from `SCAI_SECRET_KEY`; never
  returned by the API, never logged (log redaction filter covers URLs/tokens).
* Previews are proxied (`snapshot.jpg` / `stream.mjpg`) with short-lived stream
  tokens — camera URLs never reach the browser.
* Rate limiting on login, camera tests, settings, exports; security headers set;
  production CSP; audit log for every administrative action and export.
* **No facial recognition, no biometric templates, no permanent identity.**
  Anonymous IDs reset after a configurable absence (default 30 min) and on restart.
* **No video or audio stored** by default; events store metadata plus a small
  JSON evidence blob. Retention is configurable and purged hourly.
* Access must be restricted to authorised staff; display the monitoring notice
  (`Settings → Privacy`, also returned by `/api/system/info`).

See [`docs/PRIVACY.md`](docs/PRIVACY.md) for the full data inventory, DPIA checklist
and recommended signage text.

---

## 11. Known limitations

* **Small/oblique phones in wide shots are hard.** At 1080p full-room views a phone
  is often 15-25 px wide. Use a closer camera, higher inference resolution, or a
  fine-tuned model; expect lower recall otherwise.
* **The association is geometric, not pose-based.** No hand keypoints are used, so a
  student resting a hand near a phone on the desk can score as "possible use". The
  persistence threshold and manual review exist precisely for this.
* **Occluded students** (heads behind laptops, overlapping seats) are tracked with a
  gap tolerance but can lose their ID; alerts for heavily occluded people are
  downgraded to `uncertain` rather than announced.
* **HOG fallback** detects standing people only and cannot detect phones. It exists so
  the platform is demonstrable without weights — not for production alerting.
* **50 people** is a configured cap that the tracker supports; whether your hardware
  sustains the required FPS must be measured (`scripts/benchmark.py`). This repository
  makes no performance claims for untested hardware.
* ONVIF profile resolution, TensorRT and PostgreSQL are supported but require the
  optional packages/services listed in the docs.

---

## 12. Repository layout

```
backend/
  app/
    api/          auth, cameras, events, stats, settings, system, websocket routers
    core/         config, security (JWT/PBKDF2/RBAC), crypto (Fernet), logging, rate limit
    db/           models, session, seed
    services/     camera_manager, worker, events, alerts, tts, stats, onvif, audit, broadcast
    vision/       detector, tracker, association, temporal, pipeline, sources, geometry, evaluation
  scripts/        fetch_model, check_env, benchmark, prepare_dataset, train, evaluate,
                  export_onnx, generate_demo_video
  tests/          104 unit/integration tests
frontend/
  src/            pages (monitor, events, cameras, reports, settings, audit, login),
                  components, lib (api, auth, ws, voice, types)
docs/             architecture, API, deployment, training, evaluation, benchmarking,
                  privacy, operations
docker-compose.yml, Makefile, samples/
```

---

## 13. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Amber banner "Degraded detector: … no mobile-phone detection" | No YOLO weights. `python scripts/fetch_model.py`, then **Settings → Reload**. |
| `yolo11n.pt` is 132 bytes | You copied a Git LFS pointer. Use `fetch_model.py --check` and fetch the real file. |
| Preview stays black / "Waiting for first frame" | Wrong RTSP URL/path, camera busy, or codec unsupported. Use *Test connection*; try the substream URL. |
| `libGL.so.1` errors | Use `opencv-python-headless` (already in requirements), not `opencv-python`. |
| No sound | Browsers need a click: press **Enable audio**. Server output needs `espeak-ng`. |
| Many "possible use" but few alerts | Normal. Raise sensitivity only after checking real footage; lower `persistence_threshold` in small steps. |
| Too many false alerts | Raise `persistence_threshold` (0.7-0.8), raise `min_sustained_seconds`, raise `phone_conf`, and add hard negatives to training. |
| Alerts every few seconds from one student | Cooldown too low; keep ≥ 60 s. Duplicate-suppressed entries are logged, not announced. |
| High CPU, low FPS | Raise `frame_sample`, lower inference resolution, run fewer cameras per host, or use a GPU/ONNX. Check `scripts/benchmark.py`. |
| Login returns 429 | Rate limit (10/min). Wait a minute; disable with `SCAI_RATE_LIMIT_ENABLED=false` for local dev. |

---

## 14. Licence & responsible use

Deploy only with documented institutional authorisation, visible monitoring notices,
and a retention policy approved by your data-protection contact. Do not use this
system to identify individuals, to monitor private spaces (toilets, changing rooms,
homes), or to build biometric profiles. The authors provide no warranty; you are
responsible for legal compliance in your jurisdiction (e.g. GDPR, India DPDP Act,
FERPA/COPPA-style rules for minors).

Ultralytics YOLO weights/code are AGPL-3.0 (commercial licence available); OpenCV is
Apache-2.0; this project's own code is provided for educational and institutional use.
