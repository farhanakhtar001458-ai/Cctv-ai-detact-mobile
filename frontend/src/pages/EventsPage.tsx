import { useCallback, useEffect, useMemo, useState } from "react";
import { api, downloadCsv } from "../lib/api";
import { useAuth } from "../lib/auth";
import { useLive } from "../lib/ws";
import { CATEGORY_META, formatDateTime, percent, titleCase } from "../lib/format";
import type { Camera, DetectionEvent } from "../lib/types";
import { TopBar } from "../components/Layout";
import { DownloadIcon, RefreshIcon } from "../components/Icons";
import { Banner, EmptyState, Field, Modal, Spinner, StatusPill, useToast } from "../components/Primitives";

const CATEGORIES = [
  "",
  "person_detected",
  "phone_detected",
  "possible_phone_use",
  "sustained_phone_use",
  "uncertain",
];

export function EventsPage() {
  const { permissions } = useAuth();
  const live = useLive();
  const toast = useToast();
  const [events, setEvents] = useState<DetectionEvent[]>([]);
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [category, setCategory] = useState("");
  const [cameraId, setCameraId] = useState("");
  const [source, setSource] = useState("");
  const [days, setDays] = useState("7");
  const [selected, setSelected] = useState<DetectionEvent | null>(null);
  const [note, setNote] = useState("");

  const filters = useMemo(() => {
    const since = new Date(Date.now() - Number(days) * 86400_000).toISOString();
    return { category, camera_id: cameraId, source, since, limit: 300, days };
  }, [category, cameraId, source, days]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [rows, cams] = await Promise.all([
        api.events(filters as unknown as Record<string, string | number>),
        cameras.length ? Promise.resolve(cameras) : api.cameras(),
      ]);
      setEvents(rows);
      setCameras(cams as Camera[]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load events");
    } finally {
      setLoading(false);
    }
  }, [filters, cameras]);

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters.category, filters.camera_id, filters.source, filters.days]);

  // Live events open in realtime: reflect them without a manual refresh.
  useEffect(() => {
    if (!live.events.length) return;
    setEvents((prev) => {
      const map = new Map(prev.map((ev) => [ev.id, ev]));
      live.events.slice(0, 10).forEach((ev) => {
        if (map.has(ev.id)) map.set(ev.id, { ...map.get(ev.id), ...ev, camera_name: ev.camera_name || map.get(ev.id)!.camera_name });
      });
      return Array.from(map.values()).sort((a, b) => (b.started_at || "").localeCompare(a.started_at || ""));
    });
  }, [live.events]);

  const cameraName = (id: number) => cameras.find((camera) => camera.id === id)?.name || `Camera ${id}`;

  const acknowledge = async () => {
    if (!selected) return;
    try {
      await api.acknowledge(selected.id, note);
      toast.push("Event marked as reviewed", "success");
      setSelected(null);
      setNote("");
      load();
    } catch (err) {
      toast.push(err instanceof Error ? err.message : "Failed", "error");
    }
  };

  const exportCsv = async () => {
    const params = new URLSearchParams({ days: String(days) });
    if (category) params.set("category", category);
    if (cameraId) params.set("camera_id", cameraId);
    if (source) params.set("source", source);
    try {
      await downloadCsv(`/api/events/export/csv?${params.toString()}`, "classroom_events.csv");
      toast.push("CSV export downloaded", "success");
    } catch (err) {
      toast.push(err instanceof Error ? err.message : "Export failed", "error");
    }
  };

  return (
    <>
      <TopBar title="Detection events" subtitle="Review, filter and export recorded events">
        <button className="btn sm" onClick={load} disabled={loading}>
          <RefreshIcon size={13} /> Refresh
        </button>
        <button className="btn sm" onClick={exportCsv} disabled={!permissions?.can_export}>
          <DownloadIcon size={13} /> Export CSV
        </button>
      </TopBar>
      <div className="content">
        <div className="panel">
          <div className="panel-head">
            <div className="panel-title">Filters</div>
            <span className="small muted">{events.length} events shown</span>
          </div>
          <div className="panel-body">
            <div className="field-row three">
              <Field label="Category">
                <select value={category} onChange={(e) => setCategory(e.target.value)}>
                  {CATEGORIES.map((value) => (
                    <option value={value} key={value}>
                      {value ? CATEGORY_META[value as keyof typeof CATEGORY_META].label : "All categories"}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Camera">
                <select value={cameraId} onChange={(e) => setCameraId(e.target.value)}>
                  <option value="">All cameras</option>
                  {cameras.map((camera) => (
                    <option value={camera.id} key={camera.id}>
                      {camera.name}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Detection source">
                <select value={source} onChange={(e) => setSource(e.target.value)}>
                  <option value="">All sources</option>
                  <option value="model">Model detections only</option>
                  <option value="simulated">Simulated demo only</option>
                </select>
              </Field>
            </div>
            <div style={{ marginTop: 10 }}>
              <Field label="Time range">
                <select value={days} onChange={(e) => setDays(e.target.value)}>
                  <option value="1">Last 24 hours</option>
                  <option value="7">Last 7 days</option>
                  <option value="30">Last 30 days</option>
                  <option value="90">Last 90 days</option>
                </select>
              </Field>
            </div>
          </div>
        </div>

        {error ? <Banner tone="danger">{error}</Banner> : null}

        <div className="panel">
          <div className="panel-head">
            <div className="panel-title">Event log</div>
            <span className="small muted">Retention is configured in Settings → Privacy</span>
          </div>
          {loading ? (
            <div className="panel-body">
              <Spinner label="Loading events…" />
            </div>
          ) : events.length === 0 ? (
            <EmptyState title="No events match these filters" hint="Try widening the time range." />
          ) : (
            <div className="table-wrap" style={{ maxHeight: "68vh", overflowY: "auto" }}>
              <table className="data">
                <thead>
                  <tr>
                    <th>Started</th>
                    <th>Camera</th>
                    <th>Person ID</th>
                    <th>Category</th>
                    <th>Confidence</th>
                    <th>Duration</th>
                    <th>Alert</th>
                    <th>Source</th>
                    <th>Review</th>
                  </tr>
                </thead>
                <tbody>
                  {events.map((event) => {
                    const meta = CATEGORY_META[event.category] ?? CATEGORY_META.uncertain;
                    return (
                      <tr
                        key={event.id}
                        onClick={() => {
                          setSelected(event);
                          setNote(event.review_note || "");
                        }}
                        style={{ cursor: "pointer" }}
                      >
                        <td className="mono">{formatDateTime(event.started_at)}</td>
                        <td>{event.camera_name || cameraName(event.camera_id)}</td>
                        <td className="mono">{event.tracking_id || "—"}</td>
                        <td>
                          <StatusPill tone={meta.tone}>{meta.label}</StatusPill>
                        </td>
                        <td className="mono">{percent(event.confidence)}</td>
                        <td className="mono">{event.duration_s ? `${event.duration_s.toFixed(1)}s` : "open"}</td>
                        <td className="mono">{titleCase(event.alert_status)}</td>
                        <td>
                          <StatusPill tone={event.simulated ? "amber" : "blue"}>
                            {event.simulated ? "simulated" : "model"}
                          </StatusPill>
                        </td>
                        <td className="small">
                          {event.acknowledged_by ? `✓ ${event.acknowledged_by}` : "—"}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      {selected ? (
        <Modal
          title={`Event #${selected.id}`}
          onClose={() => setSelected(null)}
          footer={
            <>
              <button className="btn ghost" onClick={() => setSelected(null)}>
                Close
              </button>
              {permissions?.can_acknowledge ? (
                <button className="btn primary" onClick={acknowledge}>
                  Mark reviewed
                </button>
              ) : null}
            </>
          }
        >
          <div className="stack">
            <dl className="kv">
              <dt>Category</dt>
              <dd>{CATEGORY_META[selected.category]?.label ?? selected.category}</dd>
              <dt>Camera</dt>
              <dd>{selected.camera_name || cameraName(selected.camera_id)}</dd>
              <dt>Anonymous person ID</dt>
              <dd>{selected.tracking_id || "—"}</dd>
              <dt>Window</dt>
              <dd>
                {formatDateTime(selected.started_at)} →{" "}
                {selected.ended_at ? formatDateTime(selected.ended_at) : "still open"}
              </dd>
              <dt>Confidence</dt>
              <dd>{percent(selected.confidence)}</dd>
              <dt>Alert status</dt>
              <dd>{titleCase(selected.alert_status)}</dd>
              <dt>Recorded evidence</dt>
              <dd style={{ whiteSpace: "pre-wrap" }}>{JSON.stringify(selected.evidence, null, 2)}</dd>
            </dl>
            <Field label="Manual review note" hint="Use this for uncertain detections that need staff follow-up.">
              <textarea
                rows={3}
                value={note}
                onChange={(e) => setNote(e.target.value)}
                disabled={!permissions?.can_acknowledge}
              />
            </Field>
          </div>
        </Modal>
      ) : null}
    </>
  );
}
