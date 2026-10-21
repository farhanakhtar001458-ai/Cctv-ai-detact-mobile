# Privacy & compliance notes

This system was designed so that a school can deploy classroom monitoring **without**
creating biometric profiles. This document states exactly what is and is not
processed, and lists what a deploying institution must decide.

## 1. What the system does with data

| Data | Processed? | Stored? | Where |
|---|---|---|---|
| Video frames | Yes — in memory only, for detection | **No** | RAM; last annotated JPEG per camera for the preview |
| Annotated preview (JPEG) | Yes | **No** — held in memory, replaced each frame | RAM only (never written to disk) |
| Audio | **No** — the system never captures audio | No | — |
| Faces / biometric templates | **No** — no face detection or recognition is implemented | No | — |
| Person identity | Anonymous session ID `Person-NN` | Yes, in events | Recycles after configurable absence (default 30 min) and on restart |
| Phones / objects | Bounding boxes + confidence | Only in event `evidence` JSON | DB |
| Detection events | Yes | Yes (retention default 90 days) | DB; purge hourly |
| Camera credentials | Encrypted (Fernet, key from `SCAI_SECRET_KEY`) | Yes (ciphertext) | DB |
| Performance metrics | FPS/latency/CPU/RAM | Yes (14 days) | DB |
| Administrative actions | Yes | Yes | `audit_logs` (retained until manually pruned) |

Nothing in the codebase writes frames to disk, opens a microphone, or computes a
face embedding. If you extend the system, keep that property or document the change
and obtain new authorisation.

## 2. Anonymous tracking IDs

* IDs are integers assigned by the tracker, formatted `Person-01`, `Person-02`, …
* They are **not** derived from appearance, gait, clothing colour or any biometric.
  There is no re-identification across cameras or across sessions.
* They can be recycled; two different students may hold the same ID in different
  sessions. Events therefore log camera + timestamp + ID, never "student X".
* Reset policy: a track is dropped after `tracker_max_age` frames without detection;
  a person absent longer than `SCAI_TRACKING_ID_RESET_MINUTES` gets a fresh ID.

## 3. State vocabulary (no over-claiming)

| State | Wording used in UI and exports |
|---|---|
| `phone_detected` | "Mobile phone detected" — a phone is visible, nothing about use |
| `possible_phone_use` | "Possible phone use" — evidence below threshold |
| `sustained_phone_use` | "Sustained phone-use alert" — confidence + persistence satisfied |
| `uncertain` | "Uncertain detection — needs review" |

The system never writes "confirmed", "caught" or any language implying certainty, and
every event carries the numeric evidence and a human-readable reason.

## 4. Notice and signage

`Settings → Privacy → Monitoring notice` holds the text shown to staff; it is also
returned by `/api/system/info`. Suggested classroom signage:

> **AI-assisted classroom monitoring**
> This classroom uses a camera system that detects mobile-phone use and counts
> visible people to support classroom management. It does not use facial
> recognition, does not identify individuals, and does not record audio. Anonymous
> tracking IDs are temporary. Recorded events are retained for 90 days and are
> accessible only to authorised staff. Contact <DPO/principal> for questions or to
> exercise your rights.

## 5. Roles and least privilege

| Role | Can see | Can do |
|---|---|---|
| `viewer` | live dashboard, events, reports | nothing else |
| `supervisor` | everything above | configure/start/stop cameras, review events, export CSV, test voice |
| `admin` | everything | runtime settings, users, audit log, model reload |

Exports require `supervisor+` and are written to the audit log with the actor and
row count. Everyone with dashboard access can see monitoring data — restrict network
access (VPN/staff LAN) and use per-person accounts.

## 6. Retention

* `event_retention_days` (default 90) — older events are deleted by an hourly task and
  via `DELETE /api/events/purge`.
* `metrics_retention_days` (default 14).
* Voice clips (server TTS mode only) are written to `data/voice/`; delete them with
  the rest of `data/` during maintenance if you do not need an audio trail.
* Backups inherit your retention decisions — document them.

## 7. DPIA / risk checklist for the deploying institution

- [ ] Lawful basis identified (legitimate educational interest / consent / local law).
- [ ] Purpose limitation: only classroom management, not exam proctoring or discipline
      beyond policy.
- [ ] Necessity & proportionality: is a camera actually needed, at this resolution?
- [ ] Data-protection impact assessment completed and signed.
- [ ] Students (and parents/guardians for minors) informed; signage installed.
- [ ] Retention approved (default 90 days) and deletion verified.
- [ ] Access list reviewed; accounts removed when staff leave.
- [ ] Manual review process agreed: who reviews `uncertain` and `sustained` events,
      within what time, and with what right of reply for the student.
- [ ] Policy for false positives: staff must not sanction a student based solely on an
      automated alert; review evidence and context.
- [ ] No private areas covered (toilets, changing rooms, homes, counselling rooms).
- [ ] Vendor/model licence reviewed (Ultralytics AGPL or commercial).
- [ ] Incident procedure for data breaches; audit log available for investigations.

## 8. Things this system must never be used for

* Identifying individual students by face, appearance or gait.
* Building permanent behavioural profiles or cross-session re-identification.
* Automated punishment without human review.
* Monitoring private spaces or off-campus locations.
* Recording audio, keystrokes, screens or personal devices' content.
* Broadcasting to classroom speakers without explicit authorisation.

## 9. Data subject requests

Because events reference anonymous IDs, a student request is generally not
technically linkable — record that fact. Where a request concerns a specific time and
room, an authorised supervisor may review events for that window and provide a summary
without exposing other students' data. Log the request and response in the audit log.

## 10. Transparency in this repository

* The simulated demo feed is always labelled `SIMULATED` and `source=simulated`.
* `docs/` and the dashboard never present simulated data as model output.
* Confidence, thresholds and the reasoning string are visible for every alert, so a
  reviewer can audit *why* the system alerted.
