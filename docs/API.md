# API reference

Base URL: `http://<host>:8000`. Interactive OpenAPI docs: `/api/docs` (development
only). All routes require `Authorization: Bearer <jwt>` unless marked **public**.
Roles: `viewer` ⊂ `supervisor` ⊂ `admin`.

## Authentication

| Method | Path | Role | Description |
|---|---|---|---|
| POST | `/api/auth/login` | public | JSON `{username,password}` → `{access_token, token_type, expires_at, user}` |
| POST | `/api/auth/token` | public | OAuth2 password flow form (for Swagger "Authorize") |
| GET | `/api/auth/me` | any | Current user |
| GET | `/api/auth/permissions` | any | `{role, can_*}` capability map |
| POST | `/api/auth/change-password` | any | `{current_password,new_password}` |
| POST | `/api/auth/stream-token` | any | 10-minute token with `scope=stream` for `<img>` previews |
| GET | `/api/auth/users` | admin | List accounts |
| POST | `/api/auth/users` | admin | Create `{username,password,full_name,role}` |
| PATCH | `/api/auth/users/{id}` | admin | Update `{full_name,role,is_active}` |
| DELETE | `/api/auth/users/{id}` | admin | Delete account |

Login is rate limited (10/min/IP). Tokens are HS256, `SCAI_ACCESS_TOKEN_TTL_MINUTES`
(default 480).

## Cameras

| Method | Path | Role | Description |
|---|---|---|---|
| GET | `/api/cameras` | any | List with sanitised URL + live runtime |
| POST | `/api/cameras` | supervisor+ | Create (see schema below) |
| GET | `/api/cameras/{id}` | any | One camera |
| PATCH | `/api/cameras/{id}` | supervisor+ | Partial update; restarts the worker if running |
| DELETE | `/api/cameras/{id}` | admin | Delete camera and its events |
| POST | `/api/cameras/{id}/start` | supervisor+ | Start worker (409 if disabled or worker cap reached) |
| POST | `/api/cameras/{id}/stop` | supervisor+ | Stop worker, close open events |
| POST | `/api/cameras/{id}/restart` | supervisor+ | Restart worker |
| GET | `/api/cameras/{id}/status` | any | Worker runtime state |
| GET | `/api/cameras/{id}/analysis` | any | Latest `FrameAnalysis` (404 if not running) |
| GET | `/api/cameras/{id}/snapshot.jpg` | any / stream token | Annotated JPEG |
| GET | `/api/cameras/{id}/stream.mjpg` | any / stream token | MJPEG multipart stream |
| POST | `/api/cameras/test` | supervisor+ | Probe a stream without saving |
| POST | `/api/cameras/discover/onvif` | supervisor+ | WS-Discovery probe |
| POST | `/api/cameras/discover/onvif/stream` | supervisor+ | Resolve RTSP URI (needs `onvif-zeep`) |

Create/update body:

```json
{
  "name": "Room 101 - Front",
  "location": "Block A",
  "source_type": "rtsp",
  "url": "rtsp://192.168.1.64:554/Streaming/Channels/101",
  "username": "camera",
  "password": "secret",
  "enabled": true,
  "auto_start": true,
  "inference_width": 1280,
  "inference_height": 720,
  "frame_sample": 3,
  "detect_conf": 0.35,
  "phone_conf": 0.30,
  "persistence_threshold": 0.65,
  "alert_cooldown_s": 60,
  "phone_detection_enabled": true
}
```

`source_type ∈ rtsp | http | file | webcam | simulated`. Responses contain
`url_display` (credentials stripped) and `has_credentials` — never the secret.

## Events

| Method | Path | Role | Description |
|---|---|---|---|
| GET | `/api/events` | any | Query: `limit, offset, camera_id, category, source, tracking_id, since, until` |
| GET | `/api/events/categories` | any | Category → label map |
| GET | `/api/events/{id}` | any | Single event with evidence JSON |
| POST | `/api/events/{id}/acknowledge` | supervisor+ | `{note}` → `alert_status=acknowledged` |
| GET | `/api/events/export/csv` | supervisor+ | Query: `days, camera_id, category, source, limit` |
| DELETE | `/api/events/purge` | supervisor+ | Apply retention (`older_than_days` optional) |

Event object:

```json
{
  "id": 42, "event_uuid": "3f0c…", "camera_id": 1, "camera_name": "Room 101",
  "tracking_id": "Person-07", "category": "sustained_phone_use",
  "confidence": 0.78, "started_at": "2026-10-21T09:14:02+00:00",
  "ended_at": "2026-10-21T09:14:11+00:00", "duration_s": 9.0,
  "alert_status": "announced", "source": "model", "simulated": false,
  "evidence": {"state": "sustained_phone_use", "use_score": 0.78, "sustained_s": 3.4,
               "ambiguous": false, "occluded": false, "reason": "Use confidence 0.78 …"}
}
```

## Statistics

| Method | Path | Description |
|---|---|---|
| GET | `/api/stats/overview` | KPI bundle + detector status + camera runtimes |
| GET | `/api/stats/live` | Per-camera runtime map + totals |
| GET | `/api/stats/daily?days=14` | Per-day counts by category and source |
| GET | `/api/stats/cameras?days=7` | Per-camera totals, mean confidence, avg FPS/latency |
| GET | `/api/stats/hourly?days=1` | Sustained alerts by hour of day |

## Settings & voice

| Method | Path | Role | Description |
|---|---|---|---|
| GET | `/api/settings` | any | All settings with labels, defaults, categories |
| PUT | `/api/settings` | admin | `{values: {key: value}}` (validated, coerced) |
| POST | `/api/settings/reset` | admin | Restore defaults |
| GET | `/api/settings/voice/status` | any | Engine, output path, recent announcements |
| POST | `/api/settings/voice/test` | supervisor+ | Queue a test announcement |
| GET | `/api/settings/model` | any | Detector status |
| POST | `/api/settings/model/reload` | admin | Reload weights, restart running cameras |
| GET | `/api/voice/clip/{name}?token=` | stream token | Server-side TTS WAV |

## System

| Method | Path | Role | Description |
|---|---|---|---|
| GET | `/api/health` | public | Liveness (version, detector loaded) |
| GET | `/api/system/health` | any | Detector, workers, CPU/RAM, warnings |
| GET | `/api/system/info` | any | Versions, features, privacy flags |
| GET | `/api/audit?limit=&action=&actor=` | admin | Audit trail |

## WebSocket `/api/ws?token=<jwt>&topics=frames,events`

Client → server: `{"type":"ping","t":…}`, `{"type":"subscribe","topics":["frame","alert"]}`.

Server → client messages (`topic` field):

| topic / type | Payload |
|---|---|
| `hello` / `connected` | user, cameras runtime, recent events, recent alerts, `server_time` |
| `frame` / `frame_update` | occupancy, phones, alerts, tracks (normalised boxes), inference ms, model backend, simulated flag |
| `camera` / `camera_status` | status transitions with message |
| `event` / `event_opened` | new event row |
| `alert` / `phone_use_alert` | announced alert (with `simulated` flag and message) |
| `alert` / `alert_suppressed` | `reason: cooldown \| rate_limited` |
| `voice` / `announce` | text to speak (dashboard output) |
| `voice` / `announcement_state` | spoken/audio_url/error for server output |

Reconnect uses exponential backoff (0.8 s → 15 s); the `hello` message rehydrates state.

## Errors

| Code | Meaning |
|---|---|
| 400 | Invalid input (bad category, invalid URL scheme, missing stream URL) |
| 401 | Missing/expired token |
| 403 | Role not permitted |
| 404 | Unknown camera/event, or camera not running (analysis/snapshot) |
| 409 | Camera disabled or worker limit reached |
| 422 | Schema validation error (FastAPI detail array) |
| 429 | Rate limited (`Retry-After` header) |

Settings validation errors return `400` with a JSON map of `{key: reason}`.
