# Production deployment guide

## 1. Sizing checklist

Before deploying, answer these on the target hardware:

```bash
cd backend
python scripts/check_env.py
python scripts/benchmark.py --source sim://classroom --frames 200 \
       --resolutions 640x360,960x540,1280x720 --people 10,25,50
python scripts/benchmark.py --video /path/real_room_sample.mp4 --frames 200
```

Required analysis throughput per host:

```
fps_needed = Σ over cameras ( camera_fps_to_analyse )
camera_fps_to_analyse = min(stream_fps, desired_alerts_per_second_resolution) / frame_sample
```

A 15 fps stream with `frame_sample=3` needs **5 analysed frames/second per camera**.
If `pipeline_fps` from the benchmark is below that for N cameras, reduce resolution,
raise `frame_sample`, or add another worker host.

| Host | Typical capacity (measured, not promised) |
|---|---|
| 4-core CPU, no GPU | 1-2 cameras at 640×360 / 5 fps analysis with YOLO11n (or HOG fallback, no phone detection) |
| 8-core CPU + RTX 3060/4060 | 6-10 HD cameras at 960×540 / 5-8 fps analysis |
| Jetson Orin Nano | 2-4 cameras with ONNX/TensorRT at 640×640 |
| Server GPU (A10/L4) | 15-30 cameras at 640×640 / 5 fps, batching optional |

Treat these as starting points and verify with `benchmark.py` on **your** footage.

## 2. Docker

```bash
cp backend/.env.example backend/.env       # set SCAI_SECRET_KEY and bootstrap password
docker compose up --build -d
docker compose logs -f api
```

The image builds the React dashboard and serves everything from one process
(`http://<host>:8000`). Volumes:

* `scai-data` → `/app/backend/data` : SQLite DB, generated audio, `.secret_key`
* `scai-models` → `/app/models` : YOLO weights (`SCAI_MODEL_PATH=/app/models/yolo11n.pt`)

GPU access:

```yaml
# docker-compose.yml (override)
services:
  api:
    build:
      context: .
      args:
        TORCH_INDEX_URL: https://download.pytorch.org/whl/cu124
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
    environment:
      SCAI_MODEL_DEVICE: cuda:0
```

`docker compose` already passes `/dev/dri` and network host mode is *not* used, so
RTSP cameras must be reachable from the container network (LAN IPs work by default).

## 3. Nginx + TLS (recommended)

```nginx
server {
    listen 443 ssl http2;
    server_name classroom-monitor.school.local;

    ssl_certificate     /etc/ssl/certs/classroom.crt;
    ssl_certificate_key /etc/ssl/private/classroom.key;

    client_max_body_size 8m;
    proxy_read_timeout 3600s;          # long-lived MJPEG + WebSocket

    location /api/ws {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_read_timeout 3600s;
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffering off;           # required for MJPEG previews
    }
}
```

Then set in `backend/.env`:

```ini
SCAI_ENVIRONMENT=production
SCAI_ALLOWED_ORIGINS=https://classroom-monitor.school.local
SCAI_SECURE_COOKIES=true
SCAI_TRUST_PROXY_HEADERS=true
```

`/api/docs` and the OpenAPI schema are disabled automatically in production.

## 4. systemd (no Docker)

```ini
# /etc/systemd/system/scai.service
[Unit]
Description=Smart Classroom AI
After=network-online.target
Wants=network-online.target

[Service]
User=scai
Group=scai
WorkingDirectory=/opt/scai/backend
EnvironmentFile=/opt/scai/backend/.env
ExecStart=/opt/scai/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
Restart=on-failure
RestartSec=5
# Graceful shutdown closes camera workers and open events.
KillSignal=SIGTERM
TimeoutStopSec=20

# Hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=/opt/scai/backend/data /opt/scai/models
ProtectHome=true

[Install]
WantedBy=multi-user.target
```

> **Run exactly one uvicorn worker process per host** for camera processing. Scale
> with multiple *hosts* (camera subsets) or multiple services, not `--workers N`,
> because camera workers and the rate limiter are per-process state.

## 5. PostgreSQL

```ini
SCAI_DATABASE_URL=postgresql+psycopg://scai:STRONG_PASSWORD@db:5432/scai
```

```bash
pip install "psycopg[binary]"
```

Tables are created automatically on first start (`Base.metadata.create_all`). For
schema changes between versions, generate migrations:

```bash
pip install alembic
alembic init migrations          # then autogenerate from app.db.models.Base
```

Backups: `pg_dump scai > scai_$(date +%F).sql` nightly; retention still applies to
events, so backups shrink naturally.

## 6. Scaling across hosts

Camera runtime state is in-process, so:

1. Split cameras between hosts (each host has its own `.env` + camera rows).
2. On each host, keep `SCAI_AUTO_START_CAMERAS=true` or per-camera `auto_start`.
3. Only the host that owns a camera may start its worker — disable the camera row
   on other hosts (`enabled=false`) so operators cannot start it there by mistake.
4. Put one Nginx in front and route by path; all hosts share PostgreSQL for events.
5. Dashboards can connect to any host for read APIs; camera previews must be
   requested from the owning host (use per-host URLs or a small read-only proxy map).

A future improvement is a worker registry table with heartbeats so any host can
report on any camera; the current design favours deploy simplicity.

## 7. Voice output to a classroom speaker system

Options, in increasing order of integration effort:

1. **Monitoring PC audio (default).** Keep the dashboard open on the office PC,
   press *Enable audio*, connect the PC to the classroom amplifier (3.5 mm/USB/HDMI).
2. **Server audio device.** Set `SCAI_VOICE_OUTPUT=server`; install `espeak-ng`
   (Linux) and make sure `aplay` works for the service user. Route the host's audio
   output to the amplifier.
3. **External audio router.** Poll `GET /api/settings/voice/status` (contains
   `last_announcement.audio_url`) or subscribe to the WebSocket `voice` topic and play
   the WAV through your own PA integration.

**Never** enable a camera's two-way audio/speaker through this system implicitly:
that requires an explicit, authorised integration and the vendor's API.

## 8. Hardening checklist

- [ ] `SCAI_SECRET_KEY` set to a random 48+ byte value, stored in a secret manager.
- [ ] Bootstrap passwords changed; per-staff accounts created (no shared logins).
- [ ] TLS terminated at Nginx; `SCAI_SECURE_COOKIES=true`.
- [ ] `SCAI_ALLOWED_ORIGINS` restricted to the dashboard origin.
- [ ] Database not exposed publicly; credentials rotated.
- [ ] Camera VLAN/credentials are read-only accounts (no PTZ/admin rights).
- [ ] Dashboard reachable only from the staff network/VPN.
- [ ] Retention days approved by your DPO; `DELETE /api/events/purge` run on a schedule
      (the app also purges hourly).
- [ ] Audit log reviewed monthly; export events reviewed by a supervisor.
- [ ] Monitoring notices displayed at classroom entrances (`Settings → Privacy` text).
- [ ] Backups tested (DB dump restore), and `.secret_key` backed up (otherwise stored
      camera credentials cannot be decrypted).
- [ ] `scripts/check_env.py` returns 0 (no failures) on the deployment host.

## 9. Upgrades

```bash
git pull
cd backend && pip install -r requirements.txt -r requirements-vision.txt
cd ../frontend && npm ci && npm run build
sudo systemctl restart scai     # workers close events cleanly; dashboard reconnects
```

New settings keys get defaults automatically; removed keys are ignored.
After changing model weights, press **Settings → Reload** (or restart) and check
`/api/system/info` reports `phone_capable: true`.

## 10. Monitoring the monitor

* `GET /api/health` — liveness for your uptime checker (no auth, no secrets).
* `GET /api/system/health` — detector state, worker states, CPU/RAM, warnings.
* `GET /api/stats/overview` — `avg_fps`, `avg_inference_ms`, `simulated_events_24h`.
* Log lines to alert on: `degraded mode`, `Repeated stream errors`,
  `Phone-use alert … simulated=True` (should be zero in production!),
  `No detector available`.

If `simulated_events_24h > 0` in production, someone left the demo camera enabled —
disable it.
