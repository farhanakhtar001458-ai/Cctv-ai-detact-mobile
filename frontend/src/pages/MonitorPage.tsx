import { useCallback, useEffect, useMemo, useState } from "react";
import { api, downloadCsv } from "../lib/api";
import { useAuth } from "../lib/auth";
import { useLive, tracksByState } from "../lib/ws";
import { useVoice } from "../lib/useVoice";
import { CATEGORY_META, STATE_META, percent } from "../lib/format";
import type { Camera, DetectionEvent, StatsOverview } from "../lib/types";
import { TopBar } from "../components/Layout";
import { CameraCard } from "../components/CameraCard";
import { EventTimeline } from "../components/EventTimeline";
import { AlertFeed } from "../components/AlertFeed";
import {
  AlertIcon,
  BellIcon,
  CameraIcon,
  DownloadIcon,
  PeopleIcon,
  PhoneIcon,
  PlayIcon,
  SpeakerIcon,
  StopIcon,
  WarningIcon,
} from "../components/Icons";
import { Banner, EmptyState, Field, Modal, StatusPill, Toggle, useToast } from "../components/Primitives";

const STATE_ORDER = ["none", "phone_detected", "possible_phone_use", "sustained_phone_use", "uncertain"] as const;

function Stat({
  label,
  value,
  foot,
  tone,
  icon,
}: {
  label: string;
  value: string | number;
  foot?: string;
  tone?: "green" | "red" | "amber" | "blue";
  icon?: React.ReactNode;
}) {
  return (
    <div className={`panel stat${tone ? ` tone-${tone}` : ""}`}>
      <div className="stat-label">
        {icon}
        {label}
      </div>
      <div className="stat-value">{value}</div>
      {foot ? <div className="stat-foot">{foot}</div> : null}
    </div>
  );
}

function EventDetail({
  event,
  onClose,
  canAcknowledge,
}: {
  event: DetectionEvent;
  onClose: () => void;
  canAcknowledge: boolean;
}) {
  const toast = useToast();
  const [note, setNote] = useState(event.review_note || "");
  const [busy, setBusy] = useState(false);
  const meta = CATEGORY_META[event.category] ?? CATEGORY_META.uncertain;

  const acknowledge = async () => {
    setBusy(true);
    try {
      await api.acknowledge(event.id, note);
      toast.push("Event marked as reviewed", "success");
      onClose();
    } catch (error) {
      toast.push(error instanceof Error ? error.message : "Could not acknowledge", "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      title={`Event #${event.id} · ${meta.label}`}
      onClose={onClose}
      footer={
        <>
          <button className="btn ghost" onClick={onClose}>
            Close
          </button>
          {canAcknowledge ? (
            <button className="btn primary" onClick={acknowledge} disabled={busy}>
              {busy ? "Saving…" : "Mark reviewed"}
            </button>
          ) : null}
        </>
      }
    >
      <div className="stack">
        <div className="row" style={{ gap: 8 }}>
          <StatusPill tone={meta.tone}>{meta.label}</StatusPill>
          <StatusPill tone={event.simulated ? "amber" : "blue"}>
            {event.simulated ? "Simulated source" : "Model detection"}
          </StatusPill>
          <StatusPill tone="gray">Alert: {event.alert_status}</StatusPill>
        </div>
        <dl className="kv">
          <dt>Camera</dt>
          <dd>{event.camera_name || event.camera_id}</dd>
          <dt>Anonymous person ID</dt>
          <dd>{event.tracking_id || "—"}</dd>
          <dt>Started</dt>
          <dd>{new Date(event.started_at).toLocaleString()}</dd>
          <dt>Ended</dt>
          <dd>{event.ended_at ? new Date(event.ended_at).toLocaleString() : "still open"}</dd>
          <dt>Duration</dt>
          <dd>{event.duration_s ? `${event.duration_s.toFixed(1)} s` : "—"}</dd>
          <dt>Confidence</dt>
          <dd>{percent(event.confidence)}</dd>
          <dt>Reviewed by</dt>
          <dd>{event.acknowledged_by || "not reviewed"}</dd>
        </dl>
        <div>
          <div className="section-title">Evidence recorded at alert time</div>
          <pre
            className="mono"
            style={{
              margin: 0,
              padding: 12,
              background: "#0a1119",
              border: "1px solid var(--line)",
              borderRadius: 8,
              maxHeight: 220,
              overflow: "auto",
              fontSize: 11.5,
            }}
          >
            {JSON.stringify(event.evidence, null, 2)}
          </pre>
        </div>
        {canAcknowledge ? (
          <Field label="Review note" hint="Stored with the event for the manual review process.">
            <textarea rows={3} value={note} onChange={(e) => setNote(e.target.value)} maxLength={2000} />
          </Field>
        ) : (
          <div className="small muted">{event.review_note || "No review note."}</div>
        )}
      </div>
    </Modal>
  );
}

export function MonitorPage() {
  const live = useLive();
  const { permissions } = useAuth();
  const voice = useVoice();
  const toast = useToast();
  const [overview, setOverview] = useState<StatsOverview | null>(null);
  const [cameraConfigs, setCameraConfigs] = useState<Camera[]>([]);
  const [selectedEvent, setSelectedEvent] = useState<DetectionEvent | null>(null);
  const [sensitivity, setSensitivity] = useState(0.65);
  const [savingSensitivity, setSavingSensitivity] = useState(false);
  const [selectedCamera, setSelectedCamera] = useState<"all" | number>("all");
  const [bulkBusy, setBulkBusy] = useState(false);

  useEffect(() => {
    api
      .settings()
      .then((items) => {
        const item = items.find((entry) => entry.key === "persistence_threshold");
        if (item && typeof item.value === "number") setSensitivity(item.value);
      })
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const [data, cams] = await Promise.all([api.overview(), api.cameras()]);
        if (alive) {
          setOverview(data);
          setCameraConfigs(cams);
        }
      } catch {
        /* transient - live state still renders */
      }
    };
    load();
    const timer = window.setInterval(load, 30000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, []);

  const cameras = useMemo(() => Object.values(live.cameras), [live.cameras]);
  const running = cameras.filter((camera) => ["online", "reconnecting", "connecting"].includes(camera.status));
  const totals = useMemo(() => {
    return running.reduce(
      (acc, camera) => ({
        occupancy: acc.occupancy + camera.occupancy,
        alerts: acc.alerts + camera.active_alerts,
        possible: acc.possible + camera.possible_alerts,
        phones: acc.phones + camera.phones_visible,
        uncertain: acc.uncertain + camera.uncertain,
      }),
      { occupancy: 0, alerts: 0, possible: 0, phones: 0, uncertain: 0 },
    );
  }, [running]);

  const allTracks = useMemo(() => running.flatMap((camera) => camera.tracks || []), [running]);
  const stateCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    STATE_ORDER.forEach((state) => {
      counts[state] = tracksByState(allTracks, state).length;
    });
    return counts;
  }, [allTracks]);

  const detector = overview?.detector;
  const simulatedActive = cameras.some((camera) => camera.simulated);
  const phoneCapable = running.length ? running.some((camera) => camera.phone_capable) : detector?.phone_capable;

  const applySensitivity = useCallback(
    async (value: number) => {
      setSensitivity(value);
      if (!permissions?.can_change_settings) return;
      setSavingSensitivity(true);
      try {
        await api.updateSettings({ persistence_threshold: value });
      } catch (error) {
        toast.push(error instanceof Error ? error.message : "Could not save sensitivity", "error");
      } finally {
        setSavingSensitivity(false);
      }
    },
    [permissions?.can_change_settings, toast],
  );

  const bulkControl = useCallback(
    async (action: "start" | "stop") => {
      setBulkBusy(true);
      let changed = 0;
      for (const camera of cameraConfigs) {
        const isRunning = ["online", "connecting", "reconnecting"].includes(
          live.cameras[camera.id]?.status ?? camera.runtime?.status ?? "stopped",
        );
        if (action === "start" && (isRunning || !camera.enabled)) continue;
        if (action === "stop" && !isRunning) continue;
        try {
          if (action === "start") await api.startCamera(camera.id);
          else await api.stopCamera(camera.id);
          changed += 1;
        } catch (error) {
          toast.push(
            `${camera.name}: ${error instanceof Error ? error.message : "failed"}`,
            "error",
          );
        }
      }
      toast.push(
        changed ? `${action === "start" ? "Started" : "Stopped"} ${changed} camera(s)` : "Nothing to change",
        changed ? "success" : "info",
      );
      setBulkBusy(false);
    },
    [cameraConfigs, live.cameras, toast],
  );

  const visibleCameras = useMemo(
    () =>
      selectedCamera === "all"
        ? cameraConfigs
        : cameraConfigs.filter((camera) => camera.id === selectedCamera),
    [cameraConfigs, selectedCamera],
  );

  const enableVoice = async () => {
    voice.setEnabled(true);
    voice.test();
    try {
      await api.testVoice();
    } catch {
      /* the server may not have a local engine; the browser test is enough */
    }
  };

  return (
    <>
      <TopBar
        title="Live monitor"
        subtitle={`${running.length} of ${cameras.length} camera${cameras.length === 1 ? "" : "s"} analysing · ${live.connected ? "realtime connected" : "reconnecting"}`}
      >
        <select
          aria-label="Select camera feed"
          value={selectedCamera}
          onChange={(e) => setSelectedCamera(e.target.value === "all" ? "all" : Number(e.target.value))}
          style={{ width: 210 }}
        >
          <option value="all">All camera feeds</option>
          {cameraConfigs.map((camera) => (
            <option key={camera.id} value={camera.id}>
              {camera.name}
            </option>
          ))}
        </select>
        {permissions?.can_control_cameras ? (
          <>
            <button className="btn sm" onClick={() => bulkControl("start")} disabled={bulkBusy}>
              <PlayIcon size={13} /> Start all
            </button>
            <button className="btn sm" onClick={() => bulkControl("stop")} disabled={bulkBusy}>
              <StopIcon size={13} /> Stop all
            </button>
          </>
        ) : null}
        <button
          className="btn sm"
          onClick={() =>
            downloadCsv("/api/events/export/csv?days=1", "classroom_events_today.csv").catch(() =>
              toast.push("Export requires supervisor access", "error"),
            )
          }
          disabled={!permissions?.can_export}
          title={permissions?.can_export ? "Export today's event summary" : "Requires supervisor access"}
        >
          <DownloadIcon size={13} /> Export today
        </button>
      </TopBar>

      <div className="content">
        {/* status banners - honest about what is and is not operational */}
        {!detector?.loaded ? (
          <Banner tone="danger">
            <strong>No detection model loaded.</strong> The preview may work, but no detections or alerts
            will be produced. {detector?.error ? <span className="mono small"> ({detector.error})</span> : null}
          </Banner>
        ) : detector.degraded ? (
          <Banner tone="warn">
            <strong>Degraded detector:</strong>{" "}
            {detector.degraded_reason || "The active model cannot detect mobile phones."} Provide YOLO
            weights (see docs/TRAINING.md) to enable phone-use detection.
          </Banner>
        ) : null}
        {simulatedActive ? (
          <Banner tone="info">
            At least one camera is a <strong>simulated demo feed</strong>. Its detections are synthetic and
            tagged <span className="mono">source=simulated</span> in events and exports — they are not real
            model output. Add your own RTSP cameras on the Cameras page.
          </Banner>
        ) : null}
        {!voice.enabled ? (
          <Banner tone="info">
            <div className="row between">
              <span>
                Voice announcements are <strong>off in this browser</strong>. The backend still gates alerts;
                enable audio to hear "Mobile phone detected…" on this monitoring computer.
              </span>
              <button className="btn sm primary" onClick={enableVoice}>
                <SpeakerIcon size={13} /> Enable audio
              </button>
            </div>
          </Banner>
        ) : null}
        {voice.lastError ? <Banner tone="warn">Voice: {voice.lastError}</Banner> : null}

        <div className="grid cols-4">
          <Stat
            label="People visible"
            value={totals.occupancy}
            foot={`${stateCounts.none ?? 0} without phone evidence`}
            tone="green"
            icon={<PeopleIcon size={13} />}
          />
          <Stat
            label="Active phone-use alerts"
            value={totals.alerts}
            foot={`${totals.possible} possible · ${totals.uncertain} uncertain`}
            tone={totals.alerts ? "red" : undefined}
            icon={<AlertIcon size={13} />}
          />
          <Stat
            label="Phones in frame"
            value={totals.phones}
            foot={phoneCapable ? "Mobile phone class active" : "Detector cannot see phones"}
            tone="blue"
            icon={<PhoneIcon size={13} />}
          />
          <Stat
            label="Alerts last 24h"
            value={overview?.sustained_24h ?? "—"}
            foot={`${overview?.simulated_events_24h ?? 0} simulated · ${overview?.model_events_24h ?? 0} model`}
            tone="amber"
            icon={<WarningIcon size={13} />}
          />
        </div>

        <div className="dashboard-grid">
          <div className="stack">
            <div className="panel">
              <div className="panel-head">
                <div className="panel-title">
                  <CameraIcon size={14} /> Camera feeds
                </div>
                <div className="row small muted" style={{ gap: 12 }}>
                  <span>
                    FPS {running.length ? (overview?.avg_fps ?? 0).toFixed(1) : "—"} · latency{" "}
                    {running.length ? (overview?.avg_inference_ms ?? 0).toFixed(0) : "—"} ms
                  </span>
                </div>
              </div>
              <div className="panel-body">
                {visibleCameras.length ? (
                  <div className="camera-grid">
                    {visibleCameras.map((camera) => (
                      <CameraCard
                        key={camera.id}
                        camera={{ ...camera, runtime: live.cameras[camera.id] || camera.runtime }}
                        canControl={!!permissions?.can_control_cameras}
                      />
                    ))}
                  </div>
                ) : (
                  <EmptyState
                    title="No cameras registered"
                    hint="Add an RTSP stream or start the simulated demo camera from the Cameras page."
                  />
                )}
              </div>
            </div>

            <div className="panel">
              <div className="panel-head">
                <div className="panel-title">Detection states</div>
                <div className="legend">
                  {STATE_ORDER.map((state) => (
                    <span className="legend-item" key={state}>
                      <span className={`swatch tone-${STATE_META[state].tone}`} style={{ background: "currentColor" }} />
                      {STATE_META[state].label}
                      <strong className="mono">{stateCounts[state] ?? 0}</strong>
                    </span>
                  ))}
                </div>
              </div>
              <div className="panel-body">
                <div className="row between" style={{ alignItems: "flex-end", gap: 20 }}>
                  <div style={{ flex: "1 1 320px" }}>
                    <Field
                      label={`Alert sensitivity (persistence threshold) · ${sensitivity.toFixed(2)}`}
                      hint="Higher = fewer, more certain alerts. Only sustained phone use above this threshold is announced."
                    >
                      <input
                        type="range"
                        min={0.35}
                        max={0.95}
                        step={0.01}
                        value={sensitivity}
                        disabled={!permissions?.can_change_settings}
                        onChange={(e) => setSensitivity(Number(e.target.value))}
                        onMouseUp={(e) => applySensitivity(Number((e.target as HTMLInputElement).value))}
                        onTouchEnd={(e) => applySensitivity(Number((e.target as HTMLInputElement).value))}
                      />
                    </Field>
                  </div>
                  <div className="row" style={{ gap: 10 }}>
                    <Toggle
                      checked={voice.enabled}
                      onChange={voice.setEnabled}
                      label={voice.enabled ? "Voice alerts on" : "Voice alerts off"}
                    />
                    <button className="btn sm" onClick={voice.test} disabled={!voice.supported}>
                      <SpeakerIcon size={13} /> Test audio
                    </button>
                    {savingSensitivity ? <span className="small muted">saving…</span> : null}
                  </div>
                </div>
                <div className="small muted" style={{ marginTop: 10 }}>
                  Note: colour is never the only indicator — every state is also labelled with text and an
                  icon in the feed overlays and event timeline.
                </div>
              </div>
            </div>
          </div>

          <div className="stack">
            <div className="panel">
              <div className="panel-head">
                <div className="panel-title">
                  <BellIcon size={14} /> Alert feed
                </div>
                <div className="row small muted" style={{ gap: 8 }}>
                  <span>{live.alerts.length}</span>
                  <button className="btn sm ghost" onClick={live.clearAlerts}>
                    Clear
                  </button>
                </div>
              </div>
              <div className="panel-body">
                <AlertFeed alerts={live.alerts} />
              </div>
            </div>

            <div className="panel">
              <div className="panel-head">
                <div className="panel-title">Recent events</div>
                <span className="small muted">{live.events.length} in session</span>
              </div>
              <EventTimeline events={live.events} onSelect={setSelectedEvent} limit={24} />
            </div>

            <div className="panel">
              <div className="panel-head">
                <div className="panel-title">Voice status</div>
                <StatusPill tone={voice.enabled ? "blue" : "gray"}>
                  {voice.enabled ? "ENABLED" : "MUTED"}
                </StatusPill>
              </div>
              <div className="panel-body stack" style={{ gap: 8 }}>
                <div className="small muted">
                  Backend output: <span className="mono">{overview?.voice_output ?? "—"}</span> · server engine:{" "}
                  <span className="mono">{overview ? "see settings" : "—"}</span>
                </div>
                <div className="small">
                  Last announcement: <span className="mono">{voice.lastSpoken ?? "none yet"}</span>
                </div>
                <div className="small muted">
                  Announced this session: <strong className="mono">{voice.spokenCount}</strong>
                </div>
                <div className="row" style={{ gap: 10 }}>
                  <Field label={`Rate ${voice.rate.toFixed(2)}×`}>
                    <input
                      type="range"
                      min={0.6}
                      max={1.6}
                      step={0.05}
                      value={voice.rate}
                      onChange={(e) => voice.setRate(Number(e.target.value))}
                    />
                  </Field>
                  <Field label={`Volume ${Math.round(voice.volume * 100)}%`}>
                    <input
                      type="range"
                      min={0}
                      max={1}
                      step={0.05}
                      value={voice.volume}
                      onChange={(e) => voice.setVolume(Number(e.target.value))}
                    />
                  </Field>
                </div>
                <div className="small muted" style={{ fontFamily: "var(--mono)" }}>
                  {live.voices[0]?.type === "announce" ? `» ${live.voices[0].text}` : "Waiting for alerts…"}
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      {selectedEvent ? (
        <EventDetail
          event={selectedEvent}
          onClose={() => setSelectedEvent(null)}
          canAcknowledge={!!permissions?.can_acknowledge}
        />
      ) : null}
    </>
  );
}
