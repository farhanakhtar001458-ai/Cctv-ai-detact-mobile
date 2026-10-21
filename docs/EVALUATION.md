# Evaluation protocol

Two different things must be measured, and they must not be confused:

1. **Detection quality** — did the model find people and phones? (box-level
   precision/recall/AP)
2. **Alert quality** — did the *system* raise alerts on real phone use, and stay quiet
   when it should? (event-level precision/recall, false alerts per hour)

A detector with 0.9 AP can still produce an unacceptable alert stream. Measure both.

## 1. Detection metrics

```bash
cd backend
python scripts/evaluate.py --data datasets/classroom --split test --conf 0.35 --out reports/eval
```

Produces:

* per-class **precision, recall, F1, AP50** at the chosen confidence
* a **confidence sweep** table (choose the operating point per camera type)
* a **confusion matrix** (ground truth rows vs prediction columns, background
  row/column for misses and false positives) as SVG + Markdown

Metrics use greedy IoU matching (COCO style, `--iou 0.5` default). The implementation
lives in `backend/app/vision/evaluation.py` and is unit tested
(`backend/tests/test_evaluation.py`), including duplicate-prediction and
false-positive penalties.

Report separately for the two classes. Phones are rare: a single missed phone in a
50-person frame barely moves person recall but destroys phone recall.

## 2. Scenario matrix (mandatory before go-live)

Run the same labelled clip set through the app and record per-scenario results:

| # | Scenario | Expected behaviour |
|---|---|---|
| 1 | Empty classroom | 0 people, 0 alerts |
| 2 | Students with hands empty / writing | 0 phone alerts (watch false positives from pens/phones on desks) |
| 3 | Books, calculators, remotes, bottles in frame | no phone alerts; any are false positives to report |
| 4 | One student raises a phone and looks at it for > 5 s | `possible_phone_use` → `sustained_phone_use`, one announcement |
| 5 | Same student keeps the phone up for a minute | one announcement, then `suppressed: cooldown` events (no repeat audio) |
| 6 | Phone resting on desk, student not looking | at most `possible_phone_use`; **no** sustained alert |
| 7 | Two students close together, one phone | `uncertain` (ambiguity), no alert |
| 8 | Phone passed between students | at most one alert at a time; no double assignment in the same frame |
| 9 | Student walks in/out | new anonymous ID; no alert storm; no ID hijack of a neighbour |
| 10 | 50 people (dense exam hall) | occupancy ≈ 50, FPS reported, no ID collapse; measure real numbers |
| 11 | Heavy occlusion (laptops/heads) | occluded track downgraded to `uncertain`, not announced |
| 12 | Low light / projector only | recall reported honestly; no false alerts from bright screens |
| 13 | Camera disconnect 30 s | status `reconnecting`, automatic recovery, no crash |
| 14 | Second camera online | independent cooldowns, no cross-camera alert storms |

For each: number of true alerts, false alerts, missed alerts, and the
`use_score`/`sustained_s` at which the decision flipped (visible in the event
`evidence`).

## 3. Alert-level metrics

Define an alert window: a sustained-phone-use event is a **true alert** if a phone
was genuinely being used (annotated by a human reviewer) within ±5 s; otherwise it is
a **false alert**.

```
alert_precision = true_alerts / (true_alerts + false_alerts)
alert_recall    = true_alerts / human_labelled_phone_use_episodes
false_alerts_per_hour = false_alerts / observed_hours
```

Targets to aim for (institutional classroom pilot):

| Metric | Target | Why |
|---|---|---|
| Alert precision | ≥ 0.85 | Staff must trust the alerts |
| Alert recall | ≥ 0.60 | Missing half the incidents is tolerable, false accusations are not |
| False alerts/hour/camera | ≤ 0.5 | Above this, staff stop responding |
| Announcement duplicates | 0 | Cooldown must prevent repeats for the same person/episode |

If precision is low: raise `persistence_threshold` (0.7-0.8), raise
`min_sustained_seconds` (3-4 s), raise `phone_conf`, and add hard negatives.
If recall is low: lower `phone_conf`, increase inference resolution, fine-tune on
that camera's footage.

## 4. Threshold sensitivity study

The running system exposes thresholds at runtime; sweep them offline on your labelled
footage:

```bash
for p in 0.55 0.65 0.75 0.85; do
  curl -s -X PUT localhost:8000/api/settings -H "Authorization: Bearer $TOKEN" \
    -H 'Content-Type: application/json' -d "{\"values\":{\"persistence_threshold\":$p}}"
  # play the evaluation clips, then export events
  curl -s "localhost:8000/api/events/export/csv?days=1" -H "Authorization: Bearer $TOKEN" > events_p$p.csv
done
```

Plot alerts/hour and true alerts for each setting; pick the knee of the curve and
record it in the deployment notes.

## 5. Reproducibility

Record for every evaluation run:

* model file + SHA-256 (`sha256sum best.pt`), and the licence under which it is used;
* dataset version/commit, split seed, number of images, class counts;
* hardware (from `scripts/benchmark.py` output);
* application version (`/api/system/info`), thresholds, `frame_sample`, inference
  resolution;
* reviewer names (two independent reviewers for alert-level labels).

Store the generated markdown/JSON from `evaluate.py` and `benchmark.py` next to the
release notes. Do not commit student footage to the repository.

## 6. What this repository does **not** claim

No accuracy or FPS number in these docs describes your installation. The bundled
tests validate the *logic*, not model accuracy. The simulated demo feed produces
synthetic ground truth and must never be used as evidence of model performance.
