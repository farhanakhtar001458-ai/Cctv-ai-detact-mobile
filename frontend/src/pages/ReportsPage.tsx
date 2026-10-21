import { useEffect, useMemo, useState } from "react";
import { api, downloadCsv } from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatDateTime, percent } from "../lib/format";
import type { CameraSummary, DailySummary, StatsOverview } from "../lib/types";
import { TopBar } from "../components/Layout";
import { DownloadIcon, RefreshIcon } from "../components/Icons";
import { Banner, EmptyState, Spinner, StatusPill, useToast } from "../components/Primitives";

function Bars({ data, max }: { data: DailySummary[]; max: number }) {
  if (!data.length) return <EmptyState title="No data for this period" />;
  return (
    <div style={{ display: "grid", gap: 6 }}>
      {data.map((day) => (
        <div key={day.date} style={{ display: "grid", gridTemplateColumns: "92px 1fr 90px", gap: 10, alignItems: "center" }}>
          <span className="mono small muted">{day.date}</span>
          <div style={{ height: 16, background: "#0b1219", borderRadius: 4, overflow: "hidden", display: "flex" }}>
            <div
              style={{ width: `${(day.sustained / max) * 100}%`, background: "var(--red)", height: "100%" }}
              title={`${day.sustained} sustained alerts`}
            />
            <div
              style={{ width: `${(day.possible / max) * 100}%`, background: "var(--amber)", height: "100%" }}
              title={`${day.possible} possible-use events`}
            />
            <div
              style={{ width: `${(day.phone_detected / max) * 100}%`, background: "var(--blue)", height: "100%" }}
              title={`${day.phone_detected} phone detections`}
            />
            <div
              style={{ width: `${(day.uncertain / max) * 100}%`, background: "var(--gray)", height: "100%" }}
              title={`${day.uncertain} uncertain detections`}
            />
          </div>
          <span className="mono small" style={{ textAlign: "right" }}>
            {day.total} events
          </span>
        </div>
      ))}
    </div>
  );
}

export function ReportsPage() {
  const { permissions } = useAuth();
  const toast = useToast();
  const [days, setDays] = useState(14);
  const [daily, setDaily] = useState<DailySummary[]>([]);
  const [cameras, setCameras] = useState<CameraSummary[]>([]);
  const [overview, setOverview] = useState<StatsOverview | null>(null);
  const [loading, setLoading] = useState(true);

  const load = async () => {
    setLoading(true);
    try {
      const [d, c, o] = await Promise.all([api.daily(days), api.cameraSummary(days), api.overview()]);
      setDaily(d);
      setCameras(c);
      setOverview(o);
    } catch (err) {
      toast.push(err instanceof Error ? err.message : "Could not load reports", "error");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [days]);

  const max = useMemo(
    () => Math.max(1, ...daily.map((day) => day.sustained + day.possible + day.phone_detected + day.uncertain)),
    [daily],
  );
  const totals = useMemo(
    () =>
      daily.reduce(
        (acc, day) => ({
          total: acc.total + day.total,
          sustained: acc.sustained + day.sustained,
          possible: acc.possible + day.possible,
          simulated: acc.simulated + day.simulated,
          model: acc.model + day.model,
        }),
        { total: 0, sustained: 0, possible: 0, simulated: 0, model: 0 },
      ),
    [daily],
  );

  const exportCsv = async (scope: string) => {
    try {
      await downloadCsv(`/api/events/export/csv?days=${days}${scope}`, `classroom_events_${days}d.csv`);
      toast.push("CSV export downloaded", "success");
    } catch (err) {
      toast.push(err instanceof Error ? err.message : "Export failed", "error");
    }
  };

  return (
    <>
      <TopBar title="Reports" subtitle="Daily and camera-wise summaries, exportable for staff review">
        <select
          value={days}
          onChange={(e) => setDays(Number(e.target.value))}
          style={{ width: 150 }}
          aria-label="Report period"
        >
          <option value={1}>Last 24 hours</option>
          <option value={7}>Last 7 days</option>
          <option value={14}>Last 14 days</option>
          <option value={30}>Last 30 days</option>
          <option value={90}>Last 90 days</option>
        </select>
        <button className="btn sm" onClick={load} disabled={loading}>
          <RefreshIcon size={13} /> Refresh
        </button>
        <button className="btn sm primary" onClick={() => exportCsv("")} disabled={!permissions?.can_export}>
          <DownloadIcon size={13} /> Export CSV
        </button>
      </TopBar>

      <div className="content">
        {loading ? <Spinner label="Building report…" /> : null}

        <div className="grid cols-4">
          <div className="panel stat">
            <div className="stat-label">Events in period</div>
            <div className="stat-value">{totals.total}</div>
            <div className="stat-foot">
              {totals.model} model · {totals.simulated} simulated
            </div>
          </div>
          <div className="panel stat tone-red">
            <div className="stat-label">Sustained alerts</div>
            <div className="stat-value">{totals.sustained}</div>
            <div className="stat-foot">Threshold + persistence satisfied</div>
          </div>
          <div className="panel stat tone-amber">
            <div className="stat-label">Possible use</div>
            <div className="stat-value">{totals.possible}</div>
            <div className="stat-foot">Below alert threshold</div>
          </div>
          <div className="panel stat tone-blue">
            <div className="stat-label">Retention policy</div>
            <div className="stat-value">{overview?.retention_days ?? "—"}</div>
            <div className="stat-foot">days; video is never stored</div>
          </div>
        </div>

        {overview && overview.simulated_events_24h > 0 ? (
          <Banner tone="warn">
            This period includes <strong>{overview.simulated_events_24h}</strong> simulated events from demo
            feeds. They are labelled <span className="mono">source=simulated</span> and excluded from
            "model" counts — do not report them as real detections.
          </Banner>
        ) : null}

        <div className="panel">
          <div className="panel-head">
            <div className="panel-title">Daily summary</div>
            <div className="legend">
              <span className="legend-item">
                <span className="swatch" style={{ background: "var(--red)" }} /> Sustained
              </span>
              <span className="legend-item">
                <span className="swatch" style={{ background: "var(--amber)" }} /> Possible
              </span>
              <span className="legend-item">
                <span className="swatch" style={{ background: "var(--blue)" }} /> Phone
              </span>
              <span className="legend-item">
                <span className="swatch" style={{ background: "var(--gray)" }} /> Uncertain
              </span>
            </div>
          </div>
          <div className="panel-body">
            <Bars data={daily} max={max} />
          </div>
        </div>

        <div className="panel">
          <div className="panel-head">
            <div className="panel-title">Camera-wise summary</div>
            <span className="small muted">{cameras.length} cameras</span>
          </div>
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>Camera</th>
                  <th>Type</th>
                  <th>Events</th>
                  <th>Sustained</th>
                  <th>Possible</th>
                  <th>Uncertain</th>
                  <th>Mean confidence</th>
                  <th>Avg FPS</th>
                  <th>Avg latency</th>
                  <th>Last event</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {cameras.map((camera) => (
                  <tr key={camera.camera_id}>
                    <td>{camera.camera_name}</td>
                    <td className="mono">{camera.source_type || "—"}</td>
                    <td className="mono">{camera.total}</td>
                    <td className="mono">{camera.sustained}</td>
                    <td className="mono">{camera.possible}</td>
                    <td className="mono">{camera.uncertain}</td>
                    <td className="mono">{percent(camera.mean_confidence)}</td>
                    <td className="mono">{camera.avg_fps?.toFixed(1) ?? "—"}</td>
                    <td className="mono">{camera.avg_inference_ms?.toFixed(0) ?? "—"} ms</td>
                    <td className="small">{camera.last_event_at ? formatDateTime(camera.last_event_at) : "—"}</td>
                    <td>
                      <button
                        className="btn sm ghost"
                        onClick={() => exportCsv(`&camera_id=${camera.camera_id}`)}
                        disabled={!permissions?.can_export}
                      >
                        CSV
                      </button>
                    </td>
                  </tr>
                ))}
                {!cameras.length ? (
                  <tr>
                    <td colSpan={11} className="muted center" style={{ padding: 22 }}>
                      No events recorded for this period
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
        </div>

        <div className="panel">
          <div className="panel-head">
            <div className="panel-title">Export & compliance</div>
          </div>
          <div className="panel-body stack">
            <div className="row" style={{ gap: 10 }}>
              <StatusPill tone="green">No video stored</StatusPill>
              <StatusPill tone="green">No audio stored</StatusPill>
              <StatusPill tone="green">No facial recognition</StatusPill>
              <StatusPill tone="blue">Anonymous IDs</StatusPill>
            </div>
            <div className="small muted">
              Exports contain event metadata only (camera, timestamp, anonymous person ID, category,
              confidence, duration, alert status). Every export is written to the audit log with the
              requesting user and row count.
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
