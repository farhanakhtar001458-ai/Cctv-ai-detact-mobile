export type Role = "admin" | "supervisor" | "viewer";

export interface User {
  id: number;
  username: string;
  full_name: string;
  role: Role;
  is_active: boolean;
  created_at?: string | null;
  last_login_at?: string | null;
}

export interface Permissions {
  role: Role;
  can_acknowledge: boolean;
  can_configure_cameras: boolean;
  can_control_cameras: boolean;
  can_change_settings: boolean;
  can_manage_users: boolean;
  can_export: boolean;
}

export interface DetectorStatus {
  backend: string;
  model: string;
  loaded: boolean;
  degraded: boolean;
  phone_capable: boolean;
  device: string;
  error: string;
  warmup_ms: number;
  loaded_at: number | null;
  degraded_reason?: string;
  classes?: number;
}

export type CameraStatus = "stopped" | "connecting" | "online" | "error" | "reconnecting";
export type TrackState = "none" | "phone_detected" | "possible_phone_use" | "sustained_phone_use" | "uncertain";

export interface TrackSummary {
  track_id: number;
  label: string;
  box: number[];
  conf: number;
  state: TrackState;
  use_score: number;
  sustained_s: number;
  phone_box: number[] | null;
  phone_conf: number;
  association_score: number;
  ambiguous: boolean;
  occluded: boolean;
  reason: string;
}

export interface CameraRuntime {
  camera_id: number;
  name: string;
  source_type: string;
  simulated: boolean;
  status: CameraStatus;
  message: string;
  fps: number;
  inference_ms: number;
  total_ms: number;
  occupancy: number;
  phones_visible: number;
  active_alerts: number;
  possible_alerts: number;
  uncertain: number;
  low_light: boolean;
  frames_processed: number;
  frames_decoded: number;
  reconnects: number;
  last_frame_at: number;
  last_error: string;
  started_at: number;
  model_backend: string;
  phone_capable: boolean;
  frame_size: number[];
  notes: string[];
  tracks: TrackSummary[];
  stale: boolean;
}

export interface Camera {
  id: number;
  name: string;
  location: string;
  source_type: "rtsp" | "http" | "file" | "webcam" | "simulated";
  url_display: string;
  has_credentials: boolean;
  enabled: boolean;
  auto_start: boolean;
  inference_width: number;
  inference_height: number;
  frame_sample: number;
  detect_conf: number;
  phone_conf: number;
  persistence_threshold: number;
  alert_cooldown_s: number;
  phone_detection_enabled: boolean;
  runtime: CameraRuntime | null;
  created_at?: string;
}

export type EventCategory =
  | "person_detected"
  | "phone_detected"
  | "possible_phone_use"
  | "sustained_phone_use"
  | "uncertain";

export interface DetectionEvent {
  id: number;
  event_uuid: string;
  camera_id: number;
  camera_name: string;
  tracking_id: string;
  category: EventCategory;
  confidence: number;
  started_at: string;
  ended_at: string | null;
  duration_s: number;
  alert_status: string;
  source: "model" | "simulated";
  simulated: boolean;
  evidence: Record<string, unknown>;
  acknowledged_by: string | null;
  review_note: string;
}

export interface FrameMessage {
  topic: "frame";
  type: "frame_update";
  camera_id: number;
  camera_name: string;
  timestamp: number;
  occupancy: number;
  simulated: boolean;
  low_light: boolean;
  brightness: number;
  phones_visible: number;
  active_alerts: number;
  possible_alerts: number;
  uncertain: number;
  inference_ms: number;
  total_ms: number;
  model: { backend: string; phone_capable: boolean };
  tracks: TrackSummary[];
  notes: string[];
}

export interface AlertMessage {
  topic: "alert";
  type: "phone_use_alert" | "alert_suppressed";
  camera_id: number;
  camera_name: string;
  tracking_id: string;
  event_id: number;
  category: EventCategory;
  confidence?: number;
  simulated?: boolean;
  message?: string;
  reason?: string;
  timestamp?: number;
}

export interface VoiceMessage {
  topic: "voice";
  type: "announce" | "announcement_state" | "announce_failed";
  text?: string;
  camera_id?: number | null;
  camera_name?: string;
  category?: string;
  reason?: string;
  announcement?: {
    text: string;
    audio_url: string | null;
    spoken: boolean;
    error: string;
  };
}

export interface StatsOverview {
  server_time: string;
  cameras_total: number;
  cameras_enabled: number;
  cameras_running: number;
  occupancy_now: number;
  active_alerts_now: number;
  possible_alerts_now: number;
  phones_visible_now: number;
  events_24h: number;
  events_today: number;
  sustained_24h: number;
  possible_24h: number;
  phone_detections_24h: number;
  uncertain_24h: number;
  person_events_24h: number;
  simulated_events_24h: number;
  model_events_24h: number;
  users: number;
  detector: DetectorStatus;
  voice_enabled: boolean;
  voice_output: string;
  retention_days: number;
  camera_statuses: Record<string, CameraRuntime>;
  avg_fps: number;
  avg_inference_ms: number;
  last_metric_at: string | null;
}

export interface SettingItem {
  key: string;
  label: string;
  category: "detection" | "privacy" | "voice" | "performance" | string;
  description: string;
  default: unknown;
  value: unknown;
}

export interface VoiceStatus {
  engine: {
    available: boolean;
    engine: string;
    can_synthesize: boolean;
    can_play: boolean;
    detail: string;
  };
  outputs: string[];
  enabled: boolean;
  output: string;
  message_sustained: string;
  language: string;
  rate: number;
  volume: number;
  last_announcement: {
    text: string;
    camera_name: string;
    created_at: number;
    spoken: boolean;
    audio_url: string | null;
    error: string;
  } | null;
  history: Array<{
    text: string;
    camera_name: string;
    created_at: number;
    spoken: boolean;
    audio_url: string | null;
    error: string;
  }>;
}

export interface DailySummary {
  date: string;
  total: number;
  sustained: number;
  possible: number;
  phone_detected: number;
  uncertain: number;
  person_detected: number;
  simulated: number;
  model: number;
}

export interface CameraSummary {
  camera_id: number;
  camera_name: string;
  source_type: string;
  total: number;
  sustained: number;
  possible: number;
  uncertain: number;
  mean_confidence: number;
  last_event_at: string | null;
  avg_fps: number;
  avg_inference_ms: number;
}

export interface AuditEntry {
  id: number;
  created_at: string;
  actor: string;
  action: string;
  target: string;
  detail: string;
  ip_address: string;
}
