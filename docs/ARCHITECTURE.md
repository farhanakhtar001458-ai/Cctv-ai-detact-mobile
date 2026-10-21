# System architecture

## 1. Design goals

1. **Real-time on commodity hardware** for many cameras, with graceful degradation.
2. **Honest detections** — every event states whether it came from a model or a
   simulated source, and never claims certainty the evidence does not support.
3. **Privacy by default** — anonymous IDs, no biometrics, no stored video/audio.
4. **Modularity** — detection backend, tracker, association and alerting are
   independently replaceable and unit tested.
5. **Operable** — one process for a small site, horizontally scalable for a campus.

## 2. Process model

```
┌────────────────────────── uvicorn (FastAPI) ──────────────────────────┐
│                                                                       │
│  API routers (async)            Broadcaster (asyncio queues)          │
│   auth · cameras · events        ▲         │                          │
│   stats  · settings · system     │         ▼                          │
│                                  │   WebSocket /api/ws                │
│  CameraManager (registry)        │                                    │
│   ├── CameraWorker[1] (thread) ──┘  publishes frame/event/alert/voice │
│   ├── CameraWorker[2] (thread)      via loop.call_soon_threadsafe     │
│   └── CameraWorker[N] (max 8 default)                                 │
│                                                                       │
│  VoiceService (thread, bounded queue)  RetentionTask (hourly)         │
│  DetectorManager (process-wide model, inference lock)                 │
└───────────────────────────────────────────────────────────────────────┘
        │ SQLAlchemy                                    │ files (optional)
        ▼                                               ▼
   SQLite (WAL) / PostgreSQL                    data/voice/*.wav, data/.secret_key
```

* **Why threads for workers?** OpenCV/RTSP decoding and Ultralytics inference are
  blocking C++ calls that release the GIL for the heavy parts; threads avoid IPC
  complexity while keeping API handlers responsive. The API never blocks on video.
* **GIL contention:** the detector call is serialized with a lock (models are not
  re-entrant). With >2-3 cameras on CPU, run multiple API/worker containers instead
  of raising the thread count (see `docs/DEPLOYMENT.md`).
* **Backpressure:** each WebSocket client has a bounded queue; when full, the oldest
  frame update is dropped (never the worker thread).

## 3. Data flow per frame

```
source.read()                      # RTSP/file/webcam/simulated, reconnects handled
  └─ every Nth frame (frame_sample)
      ├─ resize to camera inference resolution
      ├─ DetectorManager.detect()            → raw detections   [stage 1]
      ├─ filter_detections()                 → people[], phones[]
      ├─ IoUTracker.update(people)           → Track[] with Person-NN  [stage 2]
      ├─ PhonePersonAssociator.associate()   → PhoneAssociation[]      [stage 3]
      ├─ TemporalAnalyzer.update()           → per-track state/score   [stage 4]
      ├─ annotate() + encode_jpeg()          → snapshot for MJPEG
      ├─ EventService.sync(analysis)         → open/close DB events    [stage 5]
      ├─ AlertCoordinator.handle()           → TTS + cooldown gates    [stage 6]
      ├─ Broadcaster.publish_threadsafe()    → dashboard (throttled)
      └─ CameraMetric every 10 s             → telemetry history
```

Metrics captured per frame: detection count, occupancy, per-track state/score,
inference ms, total ms, brightness (low-light flag), FPS EMA, reconnects.

## 4. Modules

| Module | Responsibility | Key types |
|---|---|---|
| `vision/sources.py` | Frame acquisition, reconnection, pacing, simulated classroom | `OpenCVSource`, `SimulatedSource`, `probe_stream` |
| `vision/detector.py` | Detector backends + capability reporting | `DetectorManager`, `DetectorStatus`, `Detection` |
| `vision/tracker.py` | Anonymous multi-object tracking | `IoUTracker`, `ByteTrackAdapter`, `Track` |
| `vision/association.py` | Phone↔person evidence scoring | `PhonePersonAssociator`, `PhoneAssociation` |
| `vision/temporal.py` | Sliding-window evidence, state machine, alert gating | `TemporalAnalyzer`, `TrackEvidence`, `AlertSuppressor` |
| `vision/pipeline.py` | Orchestrates stages 1-5 for one frame, annotation | `FrameAnalyzer`, `FrameAnalysis`, `annotate` |
| `vision/evaluation.py` | Precision/recall/AP/confusion math | `match_image`, `average_precision`, `build_confusion` |
| `services/worker.py` | Per-camera thread, snapshot, telemetry | `CameraWorker`, `CameraRuntime` |
| `services/camera_manager.py` | Worker registry, start/stop/restart limits | `CameraManager` |
| `services/events.py` | Event lifecycle, retention, review | `EventService`, `purge_expired`, `list_events` |
| `services/alerts.py` | Cooldown, rate limit, announcement dispatch | `AlertCoordinator` |
| `services/tts.py` | Browser/server announcement paths | `VoiceService`, `detect_engine` |
| `services/settings_store.py` | Validated runtime settings persisted in DB | `settings_store`, `SettingSpec` |
| `services/stats.py` | Dashboard aggregates, CSV rows | `overview`, `daily_summary`, `camera_summary` |
| `services/broadcast.py` | Thread → asyncio fan-out | `Broadcaster`, `Client` |
| `api/*` | HTTP/WebSocket surface, validation, RBAC | routers, `schemas.py` |
| `core/*` | Config, JWT/RBAC, credential crypto, logging, rate limits | `settings`, `security`, `crypto` |

## 5. Detection state machine

```
                      phone associated, weak evidence
        ┌──────────────┐  ─────────────────────────►  ┌──────────────────┐
        │ none         │                              │ phone_detected   │
        └──────┬───────┘  ◄─────────────────────────  └────────┬─────────┘
               │                  phone lost                   │
               │                                               │ score ≥ possible_threshold
               │                                               ▼
               │                                      ┌────────────────────┐
               │                                      │ possible_phone_use │
               │                                      └────────┬───────────┘
               │                     score ≥ persistence_threshold
               │                     AND sustained ≥ min_sustained_s
               │                                               ▼
               │   ambiguity_ratio ≥ limit /  heavy occlusion  ┌────────────────────┐
               └──────────────────────────────────────────────►│ sustained_phone_use│
                                                               └────────────────────┘
                       contradiction → uncertain
```

* `use_score = quality(presence, use-zone, head-zone, confidence) × association_factor × (1 − 0.5·ambiguity)`
  — association quality multiplies the score, so a weakly-linked phone cannot
  produce a high use score.
* Hysteresis (`0.08`) prevents flapping between `possible` and `sustained`.
* A gap longer than `gap_tolerance_s` restarts the persistence timer.

## 6. Database schema

```
users(id, username unique, full_name, password_hash, role, is_active, created_at, last_login_at)

cameras(id, name, location, source_type, url_encrypted, url_display, enabled, auto_start,
        inference_width, inference_height, frame_sample, detect_conf, phone_conf,
        persistence_threshold, alert_cooldown_s, phone_detection_enabled,
        created_at, updated_at)

detection_events(id, event_uuid unique, camera_id → cameras.id ON DELETE CASCADE,
        tracking_id, category, confidence, started_at, ended_at, duration_s,
        alert_status, source, evidence JSON, acknowledged_by, acknowledged_at, review_note)
        INDEX (camera_id, started_at), (category, started_at), (tracking_id)

camera_metrics(id, camera_id → cameras.id, recorded_at, fps, inference_ms, occupancy,
        active_alerts, cpu_percent, memory_mb, status)

audit_logs(id, created_at, actor, action, target, detail, ip_address)

app_settings(key PK, value JSON, updated_at)
```

`category ∈ {person_detected, phone_detected, possible_phone_use,
sustained_phone_use, uncertain}` · `source ∈ {model, simulated}` ·
`alert_status ∈ {none, pending, announced, acknowledged, suppressed}`.

Indices target the two dashboard queries (recent events per camera, counts by
category/time). Events are intentionally *not* one-row-per-frame: an event lives
from state entry to state exit, with `duration_s` computed on close.

## 7. Scaling

| Site size | Topology |
|---|---|
| ≤ 4 cameras, one monitor | Single process, SQLite, CPU/1 GPU |
| 5-20 cameras | 2-4 worker containers (each with `SCAI_MAX_CAMERA_WORKERS`), PostgreSQL, Redis-less rate limiting per container, Nginx in front |
| > 20 cameras | Shard cameras by worker host; central PostgreSQL; keep a single dashboard; consider a GPU per 8-12 HD streams |

Horizontal scaling requires sharing state; the current implementation keeps camera
runtime in-process, so **a camera must be started on the host that runs it**. For a
multi-host deployment, pin camera→host (e.g. one compose service per host with a
camera subset, `enabled=false` elsewhere) and load-balance only read APIs. See
`docs/DEPLOYMENT.md` for the pattern and its limitations.

## 8. Failure behaviour

| Failure | Behaviour |
|---|---|
| Stream drop | Exponential backoff reconnect (1→20 s), status `reconnecting`, preview keeps last frame |
| Model load failure | Falls back to HOG, then null; `degraded=true`, UI banner, no phone alerts |
| Frame processing exception | Logged, worker continues; 20 consecutive errors stop the worker with `error` status |
| DB unavailable | Settings/events degrade, vision keeps running; errors logged (no crash loop) |
| Slow dashboard client | Bounded queue drops frame updates only |
| Shutdown | SIGTERM → lifespan closes workers (events closed cleanly), TTS, detector |
| Overload | Configurable worker cap, frame sampling, resolution; `/api/system/health` warns on memory pressure |
