import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { getToken } from "./api";
import type {
  AlertMessage,
  CameraRuntime,
  DetectionEvent,
  FrameMessage,
  TrackSummary,
  VoiceMessage,
} from "./types";

interface LiveState {
  connected: boolean;
  connectionAttempts: number;
  lastMessageAt: number;
  cameras: Record<number, CameraRuntime>;
  frames: Record<number, FrameMessage>;
  events: DetectionEvent[];
  alerts: AlertMessage[];
  voices: VoiceMessage[];
  serverSkewMs: number;
}

interface LiveContextValue extends LiveState {
  subscribeVoice: (handler: (message: VoiceMessage) => void) => () => void;
  clearAlerts: () => void;
}

const MAX_EVENTS = 120;
const MAX_ALERTS = 50;
const MAX_VOICES = 30;

const initialState: LiveState = {
  connected: false,
  connectionAttempts: 0,
  lastMessageAt: 0,
  cameras: {},
  frames: {},
  events: [],
  alerts: [],
  voices: [],
  serverSkewMs: 0,
};

const LiveContext = createContext<LiveContextValue | null>(null);

function mergeEvent(existing: DetectionEvent[], incoming: DetectionEvent[]): DetectionEvent[] {
  const byId = new Map<number, DetectionEvent>();
  existing.forEach((ev) => byId.set(ev.id, ev));
  incoming.forEach((ev) => byId.set(ev.id, ev));
  return Array.from(byId.values())
    .sort((a, b) => (b.started_at || "").localeCompare(a.started_at || ""))
    .slice(0, MAX_EVENTS);
}

export function LiveProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<LiveState>(initialState);
  const voiceHandlers = useRef(new Set<(message: VoiceMessage) => void>());
  const socketRef = useRef<WebSocket | null>(null);
  const retryRef = useRef<number>(0);
  const closedRef = useRef(false);

  const subscribeVoice = useCallback((handler: (message: VoiceMessage) => void) => {
    voiceHandlers.current.add(handler);
    return () => voiceHandlers.current.delete(handler);
  }, []);

  const clearAlerts = useCallback(() => setState((prev) => ({ ...prev, alerts: [] })), []);

  useEffect(() => {
    closedRef.current = false;
    let timer: number | undefined;

    const connect = () => {
      const token = getToken();
      if (!token) return;
      const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      const url = `${protocol}//${window.location.host}/api/ws?token=${encodeURIComponent(token)}`;
      let socket: WebSocket;
      try {
        socket = new WebSocket(url);
      } catch {
        scheduleReconnect();
        return;
      }
      socketRef.current = socket;

      socket.onopen = () => {
        retryRef.current = 0;
        setState((prev) => ({ ...prev, connected: true }));
      };

      socket.onmessage = (raw) => {
        let message: Record<string, unknown>;
        try {
          message = JSON.parse(raw.data as string);
        } catch {
          return;
        }
        const topic = message.topic as string;
        const now = Date.now();
        if (topic === "hello") {
          const cameras = (message.cameras as Record<number, CameraRuntime>) || {};
          const recent = (message.recent_events as DetectionEvent[]) || [];
          const alerts = ((message.recent_alerts as Array<Record<string, unknown>>) || [])
            .filter((m) => m.topic === "alert") as unknown as AlertMessage[];
          const skew = typeof message.server_time === "number" ? message.server_time * 1000 - now : 0;
          setState((prev) => ({
            ...prev,
            cameras,
            events: mergeEvent(prev.events, recent),
            alerts: alerts.slice(-MAX_ALERTS).reverse(),
            serverSkewMs: skew,
            lastMessageAt: now,
          }));
          return;
        }
        if (topic === "frame") {
          const frame = message as unknown as FrameMessage;
          setState((prev) => {
            const cameras = { ...prev.cameras };
            const runtime = cameras[frame.camera_id];
            if (runtime) {
              cameras[frame.camera_id] = {
                ...runtime,
                occupancy: frame.occupancy,
                phones_visible: frame.phones_visible,
                active_alerts: frame.active_alerts,
                possible_alerts: frame.possible_alerts,
                uncertain: frame.uncertain,
                low_light: frame.low_light,
                inference_ms: frame.inference_ms,
                total_ms: frame.total_ms,
                tracks: frame.tracks,
                model_backend: frame.model.backend,
                phone_capable: frame.model.phone_capable,
                simulated: frame.simulated,
                last_frame_at: frame.timestamp,
                notes: frame.notes,
              };
            }
            return {
              ...prev,
              cameras,
              frames: { ...prev.frames, [frame.camera_id]: frame },
              lastMessageAt: now,
            };
          });
          return;
        }
        if (topic === "camera") {
          const cameraId = message.camera_id as number;
          setState((prev) => {
            const runtime = { ...(prev.cameras[cameraId] || {}), ...(message as Partial<CameraRuntime>) };
            return { ...prev, cameras: { ...prev.cameras, [cameraId]: runtime as CameraRuntime } };
          });
          return;
        }
        if (topic === "event") {
          const event = message.event as DetectionEvent | undefined;
          if (!event) return;
          const normalized: DetectionEvent = {
            ...event,
            simulated: event.source === "simulated",
            evidence: event.evidence || {},
            camera_name: event.camera_name || "",
            duration_s: event.duration_s || 0,
            ended_at: event.ended_at || null,
            acknowledged_by: event.acknowledged_by || null,
            review_note: event.review_note || "",
          };
          setState((prev) => ({ ...prev, events: mergeEvent(prev.events, [normalized]) }));
          return;
        }
        if (topic === "alert") {
          const alert = message as unknown as AlertMessage;
          setState((prev) => ({ ...prev, alerts: [alert, ...prev.alerts].slice(0, MAX_ALERTS) }));
          return;
        }
        if (topic === "voice") {
          const voice = message as unknown as VoiceMessage;
          setState((prev) => ({ ...prev, voices: [voice, ...prev.voices].slice(0, MAX_VOICES) }));
          voiceHandlers.current.forEach((handler) => handler(voice));
        }
      };

      socket.onclose = () => {
        setState((prev) => ({ ...prev, connected: false }));
        if (!closedRef.current) scheduleReconnect();
      };
      socket.onerror = () => {
        socket.close();
      };
    };

    const scheduleReconnect = () => {
      retryRef.current += 1;
      const delay = Math.min(15000, 800 * Math.pow(1.6, Math.min(retryRef.current, 8)));
      setState((prev) => ({ ...prev, connectionAttempts: retryRef.current }));
      timer = window.setTimeout(connect, delay);
    };

    connect();
    const ping = window.setInterval(() => {
      if (socketRef.current?.readyState === WebSocket.OPEN) {
        socketRef.current.send(JSON.stringify({ type: "ping", t: Date.now() }));
      }
    }, 20000);

    return () => {
      closedRef.current = true;
      window.clearInterval(ping);
      if (timer) window.clearTimeout(timer);
      socketRef.current?.close();
      socketRef.current = null;
    };
  }, []);

  const value = useMemo<LiveContextValue>(
    () => ({ ...state, subscribeVoice, clearAlerts }),
    [state, subscribeVoice, clearAlerts],
  );

  return <LiveContext.Provider value={value}>{children}</LiveContext.Provider>;
}

export function useLive(): LiveContextValue {
  const context = useContext(LiveContext);
  if (!context) throw new Error("useLive must be used inside LiveProvider");
  return context;
}

export function tracksByState(tracks: TrackSummary[], state: TrackSummary["state"]): TrackSummary[] {
  return tracks.filter((t) => t.state === state);
}
