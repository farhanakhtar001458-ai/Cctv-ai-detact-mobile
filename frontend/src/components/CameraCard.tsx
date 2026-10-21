import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../lib/api";
import { useLive } from "../lib/ws";
import { STATE_META, formatTime, percent } from "../lib/format";
import type { Camera, CameraRuntime, TrackSummary } from "../lib/types";
import { CameraIcon, PlayIcon, RefreshIcon, StopIcon, WarningIcon } from "./Icons";
import { StatusPill, useToast } from "./Primitives";

const STATUS_TONE: Record<string, "green" | "amber" | "red" | "gray"> = {
  online: "green",
  connecting: "amber",
  reconnecting: "amber",
  error: "red",
  stopped: "gray",
};

/**
 * Authorises the live preview once with a short-lived stream token.
 *
 * The preview is an MJPEG multipart stream (`multipart/x-mixed-replace`), which
 * every browser renders inside an <img>. Snapshots are used as a fallback when
 * the stream cannot be established. The RTSP URL and camera credentials never
 * reach the browser - the server proxies annotated frames.
 */
function useStreamUrl(
  cameraId: number,
  running: boolean,
): { url: string | null; snapshotUrl: string | null; failure: string | null } {
  const [token, setToken] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  useEffect(() => {
    let canceled = false;
    if (!running) {
      setToken(null);
      return;
    }
    const load = async () => {
      try {
        const result = await api.streamToken();
        if (!canceled) {
          setFailure(null);
          setToken(result.token);
        }
      } catch (error) {
        if (!canceled) setFailure(error instanceof Error ? error.message : "Could not authorise preview");
      }
    };
    load();
    // Refresh the short-lived stream token well before it expires (10 min TTL).
    const timer = window.setInterval(load, 7 * 60 * 1000);
    return () => {
      canceled = true;
      window.clearInterval(timer);
    };
  }, [cameraId, running]);

  const encoded = token ? encodeURIComponent(token) : "";
  return {
    url: token ? `/api/cameras/${cameraId}/stream.mjpg?token=${encoded}` : null,
    snapshotUrl: token ? `/api/cameras/${cameraId}/snapshot.jpg?token=${encoded}` : null,
    failure,
  };
}

function TrackChips({ tracks }: { tracks: TrackSummary[] }) {
  if (!tracks.length) return null;
  const priority: Record<TrackSummary["state"], number> = {
    sustained_phone_use: 0,
    possible_phone_use: 1,
    uncertain: 2,
    phone_detected: 3,
    none: 4,
  };
  const sorted = [...tracks].sort((a, b) => priority[a.state] - priority[b.state] || a.track_id - b.track_id);
  return (
    <div className="track-list" aria-label="Tracked people in this camera">
      {sorted.map((track) => {
        const meta = STATE_META[track.state];
        return (
          <span
            key={track.track_id}
            className={`track-chip tone-${meta.tone}`}
            title={`${meta.label}${track.reason ? ` — ${track.reason}` : ""}`}
          >
            {track.label}
            <span className="score">{meta.short}</span>
            {track.state !== "none" ? <span className="score">{percent(track.use_score)}</span> : null}
            {track.ambiguous ? <span className="score">AMBIG</span> : null}
          </span>
        );
      })}
    </div>
  );
}

export function CameraCard({
  camera,
  canControl,
  compact = false,
}: {
  camera: Camera;
  canControl: boolean;
  compact?: boolean;
}) {
  const live = useLive();
  const toast = useToast();
  const runtime: CameraRuntime | undefined = live.cameras[camera.id] || camera.runtime || undefined;
  const running = !!runtime && ["online", "connecting", "reconnecting"].includes(runtime.status);
  const { url, snapshotUrl, failure } = useStreamUrl(camera.id, running);
  const [busy, setBusy] = useState(false);
  const [imgError, setImgError] = useState(false);
  const [snapshotTick, setSnapshotTick] = useState(0);
  const imgRef = useRef<HTMLImageElement | null>(null);

  // If the MJPEG stream fails (proxy/browser quirk), poll annotated snapshots.
  useEffect(() => {
    if (!imgError || !snapshotUrl) return;
    const timer = window.setInterval(() => setSnapshotTick((tick) => tick + 1), 750);
    return () => window.clearInterval(timer);
  }, [imgError, snapshotUrl]);

  useEffect(() => {
    setImgError(false);
  }, [url]);

  const status = runtime?.status ?? "stopped";
  const tone = STATUS_TONE[status] ?? "gray";
  const tracks = useMemo(() => runtime?.tracks ?? [], [runtime]);

  const control = async (action: "start" | "stop" | "restart") => {
    setBusy(true);
    try {
      if (action === "start") await api.startCamera(camera.id);
      else if (action === "stop") await api.stopCamera(camera.id);
      else {
        await api.stopCamera(camera.id);
        await api.startCamera(camera.id);
      }
      toast.push(
        `${camera.name}: ${action === "stop" ? "monitoring stopped" : "monitoring started"}`,
        "success",
      );
    } catch (error) {
      toast.push(`${camera.name}: ${error instanceof Error ? error.message : "action failed"}`, "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="panel camera-card">
      <div className="camera-head">
        <div style={{ minWidth: 0 }}>
          <div className="camera-name">
            <CameraIcon size={15} />
            <span
              style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: 240 }}
            >
              {camera.name}
            </span>
          </div>
          <div className="camera-meta">
            <span>{camera.location || "Unassigned location"}</span>
            <span className="mono" style={{ fontSize: 10.5 }}>
              {camera.source_type.toUpperCase()}
            </span>
            {runtime?.simulated ? (
              <StatusPill tone="amber" title="Synthetic feed: detections are simulated, not real model output">
                SIMULATED
              </StatusPill>
            ) : null}
          </div>
        </div>
        <div className="row" style={{ gap: 8 }}>
          <StatusPill tone={tone}>{status.toUpperCase()}</StatusPill>
          {canControl ? (
            <>
              {running ? (
                <button
                  className="btn sm"
                  disabled={busy}
                  onClick={() => control("stop")}
                  title="Stop monitoring this camera"
                >
                  <StopIcon size={13} /> Stop
                </button>
              ) : (
                <button
                  className="btn sm primary"
                  disabled={busy}
                  onClick={() => control("start")}
                  title="Start monitoring this camera"
                >
                  <PlayIcon size={13} /> Start
                </button>
              )}
              <button
                className="btn sm ghost icon"
                disabled={busy || !running}
                onClick={() => control("restart")}
                title="Restart the stream worker"
                aria-label="Restart"
              >
                <RefreshIcon size={13} />
              </button>
            </>
          ) : null}
        </div>
      </div>

      <div className="camera-view">
        {runtime?.simulated ? (
          <div className="watermark">SIMULATED FEED · NOT REAL CCTV DETECTION</div>
        ) : null}
        {!running ? (
          <div className="camera-placeholder">
            <CameraIcon size={26} />
            <div>Monitoring stopped</div>
            <div style={{ fontSize: 11 }}>
              {camera.enabled ? "Press Start to begin analysis" : "Camera is disabled in configuration"}
            </div>
          </div>
        ) : failure ? (
          <div className="camera-placeholder">
            <WarningIcon size={24} />
            <div>{failure}</div>
          </div>
        ) : url && !imgError ? (
          <img
            ref={imgRef}
            src={url}
            alt={`Annotated preview from ${camera.name}`}
            onError={() => setImgError(true)}
          />
        ) : snapshotUrl ? (
          <img
            src={`${snapshotUrl}&t=${snapshotTick}`}
            alt={`Annotated snapshot from ${camera.name}`}
          />
        ) : (
          <div className="camera-placeholder">
            <CameraIcon size={24} />
            <div>Waiting for first frame…</div>
            {runtime?.message ? <div style={{ fontSize: 11 }}>{runtime.message}</div> : null}
          </div>
        )}
        {running ? (
          <div className="overlay-stat">
            <span className="overlay-chip">⏱ {formatTime(runtime?.last_frame_at)}</span>
            <span className="overlay-chip">{(runtime?.fps ?? 0).toFixed(1)} fps</span>
            <span className="overlay-chip">{(runtime?.inference_ms ?? 0).toFixed(0)} ms</span>
          </div>
        ) : null}
      </div>

      <div className="camera-foot">
        <div className="metric-strip">
          <span>
            People <b>{runtime?.occupancy ?? 0}</b>
          </span>
          <span>
            Phones <b>{runtime?.phones_visible ?? 0}</b>
          </span>
          <span style={{ color: (runtime?.active_alerts ?? 0) > 0 ? "var(--red)" : undefined }}>
            Alerts <b>{runtime?.active_alerts ?? 0}</b>
          </span>
          <span>
            Possible <b>{runtime?.possible_alerts ?? 0}</b>
          </span>
          {runtime?.low_light ? <span style={{ color: "var(--amber)" }}>LOW LIGHT</span> : null}
          {runtime && !runtime.phone_capable ? (
            <span style={{ color: "var(--amber)" }} title="The active detector cannot detect phones">
              NO PHONE MODEL
            </span>
          ) : null}
        </div>
        <div className="small muted mono">{runtime?.model_backend || "—"}</div>
      </div>

      {!compact && tracks.length ? <TrackChips tracks={tracks} /> : null}
      {runtime?.notes?.length ? (
        <div style={{ padding: "7px 13px", borderTop: "1px solid var(--line)", fontSize: 11, color: "var(--amber)" }}>
          {runtime.notes.join(" · ")}
        </div>
      ) : null}
      {runtime?.last_error ? (
        <div style={{ padding: "7px 13px", borderTop: "1px solid var(--line)", fontSize: 11, color: "var(--red)" }}>
          Last error: {runtime.last_error}
        </div>
      ) : null}
    </div>
  );
}
