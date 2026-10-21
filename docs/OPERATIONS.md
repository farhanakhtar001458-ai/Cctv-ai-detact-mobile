# Operations runbook

## Daily

| Check | Command / screen | Action if wrong |
|---|---|---|
| Cameras online | Dashboard header `N of M cameras analysing` | Open Cameras → status/message; restart worker |
| Detector healthy | Banner absent, `Settings → Detector` shows `LOADED`, `phone-capable` | `scripts/fetch_model.py --check`; Reload |
| No simulated feed in production | `/api/stats/overview` → `simulated_events_24h` = 0 | Disable the demo camera |
| Alerts reviewed | Events page, filter `sustained_phone_use`, unacknowledged | Supervisor reviews + acknowledges with note |
| Disk | `df -h` on data volume | Retention/housekeeping; check `data/voice` |

## Weekly

* Review `uncertain` events and record notes — they are the training signal for model
  improvement.
* Check `suppressed` events: if a single student generates dozens, the cooldown is
  working; if *many* students do, retune thresholds (too sensitive).
* Inspect `camera_metrics` trends (`/api/stats/cameras`) for FPS/latency drift.
* Confirm backups of the database (and `data/.secret_key`) exist.

## Common procedures

### Restart a camera worker

Dashboard → Cameras → **Restart**, or:

```bash
curl -X POST localhost:8000/api/cameras/3/restart -H "Authorization: Bearer $TOKEN"
```

Open events are closed cleanly on stop (duration computed), so restarts do not leave
dangling rows.

### Change detection sensitivity

Monitor → **Alert sensitivity** (persistence) or Settings → Detection →
`persistence_threshold`. Guidance:

* too many alerts → 0.70-0.80 and `min_sustained_seconds` 3;
* missing obvious use → 0.55-0.60 and raise inference resolution.

Record the change in the deployment notes; it changes alert statistics.

### Rotate a camera password

Cameras → Edit → enter only the new password (URL unchanged) → Save → Restart.
The stored ciphertext is replaced; the plaintext never appears in API responses.

### Rotate `SCAI_SECRET_KEY`

Changing the key invalidates JWTs **and** makes stored camera credentials
undecryptable. Procedure:

1. Decrypt-and-reenter: note camera URLs/credentials, or keep the old key.
2. Set the new key in `.env`, restart.
3. Edit each camera and re-enter its password. The worker logs
   `Stored stream credential cannot be decrypted` until you do.
4. All users must log in again.

### Add a staff account

Settings → Staff accounts → Add user → role. Minimum password 8 characters; the user
should change it at first login (`Settings → Change my password`).

### Export events for a meeting

Events → set time range → **Export CSV** (supervisor+). The export is logged with your
username and row count. CSV contains metadata only — no frames, no names.

### Apply retention early

```bash
curl -X DELETE "localhost:8000/api/events/purge?older_than_days=30" -H "Authorization: Bearer $TOKEN"
```

### Model update

```bash
# copy new weights, then
python scripts/fetch_model.py --check
curl -X POST localhost:8000/api/settings/model/reload -H "Authorization: Bearer $TOKEN"
```

Reloading restarts running camera workers automatically.

## Incident playbook

| Symptom | Likely cause | Steps |
|---|---|---|
| All cameras `reconnecting` | network/VLAN change, switch reboot | verify ping to camera, check credentials, restart workers |
| `Repeated stream errors - worker stopped` | camera offline / path wrong / codec | correct URL, restart; check camera with `Test connection` |
| `No detector available` | weights missing/corrupt, torch import error | `check_env.py`, `fetch_model.py --check`, reinstall vision requirements |
| CPU pegged, previews lag | too many cameras or low frame_sample | raise `frame_sample`, lower resolution, move cameras to another host |
| DB locked (SQLite) | concurrent writes/backup | stop backup job during writes; migrate to PostgreSQL for >4 cameras |
| Login 429 for everyone | brute-force attempts or spam | check `audit_logs` for `login_failed`, block source IP, wait for window |
| Alerts without audio | browser audio not enabled | press **Enable audio**; check OS volume/output device |
| Audio repeats | cooldown misconfigured | `alert_cooldown_seconds` ≥ 60, `voice_max_per_minute` 4 |

## Log locations and what to grep

* stdout/journald (Docker: `docker compose logs -f api`).
* Credentials are redacted automatically; still avoid pasting full logs publicly.
* Grep targets:

```bash
journalctl -u scai | grep -E "Phone-use alert|degraded|No detector|Repeated stream|ERROR"
```

`Phone-use alert … simulated=True` in production means the demo camera is running.

## Clean shutdown

```bash
sudo systemctl stop scai      # SIGTERM → workers close events, TTS stopped, model freed
```

Do not `kill -9`: open events would remain open (they can be closed by restarting the
camera or by manual retention purge).

## Capacity watch

`/api/system/health` warnings:

* `No detector model loaded` → no detections.
* `Degraded detector` → phone detection unavailable.
* `Memory pressure is high` → reduce cameras/resolution now, before OOM.
