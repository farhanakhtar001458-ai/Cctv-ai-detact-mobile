# Hardware benchmarking guide

The product requirement is "up to 50 simultaneously visible people". The software
supports it; whether **your** host sustains it must be measured. Run this on the
actual deployment machine, with the actual cameras.

## 1. What to measure

| Metric | Where it comes from | Target |
|---|---|---|
| Analysis FPS per camera | `/api/cameras/{id}/status` → `fps`; `benchmark.py` | ≥ stream_fps / frame_sample |
| Frame latency (p50/p95) | `benchmark.py`, `inference_ms` in status | p95 < 250 ms for interactive alerting |
| End-to-end alert latency | time from first evidence frame → WebSocket `phone_use_alert` | ≤ `min_sustained_seconds` + 2 × p95 |
| CPU % / RAM | `/api/system/health`, `CameraMetric` rows | CPU < 80 % sustained, RAM headroom > 25 % |
| GPU utilisation / VRAM | `nvidia-smi` (benchmark captures it) | GPU < 90 %, VRAM < 85 % |
| Precision / recall | `scripts/evaluate.py` on labelled clips | see `docs/EVALUATION.md` |
| False alerts per hour | event CSV vs human review | ≤ 0.5 / camera / hour |

## 2. Offline pipeline benchmark

```bash
cd backend

# synthetic load (no camera needed) - the fastest way to size hardware
python scripts/benchmark.py --source sim://classroom --frames 200 \
        --resolutions 640x360,960x540,1280x720

# crowd scaling: does the tracker hold up at 50 people?
python scripts/benchmark.py --source sim://classroom --frames 150 \
        --people 10,25,50 --resolutions 960x540

# real footage (recommended): record 60 s from each camera first
python scripts/benchmark.py --video /data/clips/room101_60s.mp4 --frames 200 \
        --resolutions 960x540,1280x720

# live RTSP (measures end-to-end with decoding and network)
python scripts/benchmark.py --source rtsp --video 'rtsp://user:pass@cam/stream' --frames 300
```

Output: `benchmarks/benchmark_<timestamp>.md` + `.json` with host info, detector
backend, per-configuration latency percentiles, CPU/RAM/GPU usage.

Reading the numbers:

* `pipeline_fps` is analysis throughput (no preview encoding). Divide by N cameras
  to see whether the host keeps up.
* `inference_ms_mean` is the model call; the remainder of `latency_ms` is resize +
  tracking + association + temporal scoring (typically 2-8 ms for 50 people).
* With the HOG fallback the report warns that phone detection is unavailable — do not
  size a phone-detection deployment from HOG numbers.

## 3. Live system benchmark

```bash
# start the camera in the dashboard, then sample status for 2 minutes
for i in $(seq 1 12); do
  curl -s localhost:8000/api/cameras/1/status -H "Authorization: Bearer $TOKEN" \
    | jq '{fps, inference_ms, total_ms, occupancy, active_alerts, reconnects}'
  sleep 10
done
curl -s localhost:8000/api/system/health -H "Authorization: Bearer $TOKEN" | jq
```

For alert latency, open the dashboard console and time the gap between the first
amber chip and the red alert with the spoken announcement (or log the WebSocket
`timestamp` of `phone_use_alert` against the event `started_at`).

## 4. Capacity planning worksheet

```
cameras                     = 8
stream fps                  = 15
frame_sample                = 3
analysed fps per camera     = 5
required total analysis fps = 40

measured pipeline_fps (960x540, YOLO11n, RTX 4060) = 96
headroom                    = 96 / 40 = 2.4x   → one GPU host is enough
```

If `headroom < 1.3`, do one of:

1. increase `frame_sample` (e.g. 3 → 5): 40 % less compute, slightly slower alerts;
2. lower inference resolution to 640×360 for wide rooms without small phones;
3. switch `yolo11n` → ONNX Runtime / TensorRT;
4. split cameras across hosts (`docs/DEPLOYMENT.md` §6).

## 5. Stress and failure tests

| Test | Command / action | Pass criteria |
|---|---|---|
| 50 people | `benchmark.py --people 50` | 50 distinct IDs, no crash, latency reported |
| Camera drop | `iptables -A INPUT -s <cam_ip> -j DROP` for 30 s | `reconnecting` then auto-recovery, no orphan events |
| Invalid stream | add camera with a wrong path, start | status `error`, message shown, API still responsive |
| Weak host | run benchmark while `stress-ng --cpu 4` | worker keeps running, FPS drops, UI shows real numbers |
| Slow client | open 10 dashboard tabs | worker FPS unaffected (bounded queues) |
| Long run | 8 h continuous | RAM stable (no leak), retention task runs, disk flat |
| Restart | `systemctl restart scai` | open events closed, cameras auto-start per config |

## 6. Recording results

Keep `benchmarks/*.md` with the release. Include:

* host CPU/GPU/RAM, OS, PyTorch/CUDA/Ultralytics versions (`scripts/check_env.py`);
* model + SHA-256, inference resolution, frame sample, thresholds;
* per-camera FPS/latency, alert latency, CPU/GPU/RAM;
* alert precision/recall from the evaluation protocol;
* anything that failed (and what you changed).

Never publish a benchmark table that mixes simulated detections with model runs
without labelling which is which.
