import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { useLive } from "../lib/ws";
import { formatDateTime } from "../lib/format";
import type { Camera } from "../lib/types";
import { TopBar } from "../components/Layout";
import { CameraCard } from "../components/CameraCard";
import { PlusIcon, RefreshIcon, WarningIcon } from "../components/Icons";
import { Banner, EmptyState, Field, Modal, StatusPill, Toggle, useToast } from "../components/Primitives";

interface CameraForm {
  id?: number;
  name: string;
  location: string;
  source_type: Camera["source_type"];
  url: string;
  username: string;
  password: string;
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
}

const defaultForm = (): CameraForm => ({
  name: "",
  location: "",
  source_type: "rtsp",
  url: "rtsp://192.168.1.64:554/Streaming/Channels/101",
  username: "",
  password: "",
  enabled: true,
  auto_start: false,
  inference_width: 1280,
  inference_height: 720,
  frame_sample: 3,
  detect_conf: 0.35,
  phone_conf: 0.3,
  persistence_threshold: 0.65,
  alert_cooldown_s: 60,
  phone_detection_enabled: true,
});

export function CamerasPage() {
  const { permissions } = useAuth();
  const live = useLive();
  const toast = useToast();
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [loading, setLoading] = useState(true);
  const [form, setForm] = useState<CameraForm | null>(null);
  const [saving, setSaving] = useState(false);
  const [testResult, setTestResult] = useState<string | null>(null);
  const [testing, setTesting] = useState(false);
  const [discovering, setDiscovering] = useState(false);
  const [devices, setDevices] = useState<Array<Record<string, string>>>([]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setCameras(await api.cameras());
    } catch (err) {
      toast.push(err instanceof Error ? err.message : "Could not load cameras", "error");
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    load();
  }, [load]);

  const openNew = () => {
    setForm(defaultForm());
    setTestResult(null);
  };

  const openEdit = (camera: Camera) => {
    setForm({
      id: camera.id,
      name: camera.name,
      location: camera.location,
      source_type: camera.source_type,
      url: "",
      username: "",
      password: "",
      enabled: camera.enabled,
      auto_start: camera.auto_start,
      inference_width: camera.inference_width,
      inference_height: camera.inference_height,
      frame_sample: camera.frame_sample,
      detect_conf: camera.detect_conf,
      phone_conf: camera.phone_conf,
      persistence_threshold: camera.persistence_threshold,
      alert_cooldown_s: camera.alert_cooldown_s,
      phone_detection_enabled: camera.phone_detection_enabled,
    });
    setTestResult(null);
  };

  const update = <K extends keyof CameraForm>(key: K, value: CameraForm[K]) =>
    setForm((prev) => (prev ? { ...prev, [key]: value } : prev));

  const payload = (): Record<string, unknown> => {
    if (!form) return {};
    const data: Record<string, unknown> = {
      name: form.name,
      location: form.location,
      source_type: form.source_type,
      enabled: form.enabled,
      auto_start: form.auto_start,
      inference_width: form.inference_width,
      inference_height: form.inference_height,
      frame_sample: form.frame_sample,
      detect_conf: form.detect_conf,
      phone_conf: form.phone_conf,
      persistence_threshold: form.persistence_threshold,
      alert_cooldown_s: form.alert_cooldown_s,
      phone_detection_enabled: form.phone_detection_enabled,
    };
    if (form.url) data.url = form.url;
    if (form.username) data.username = form.username;
    if (form.password) data.password = form.password;
    return data;
  };

  const save = async () => {
    if (!form) return;
    setSaving(true);
    try {
      if (form.id) await api.updateCamera(form.id, payload());
      else await api.createCamera(payload());
      toast.push("Camera saved", "success");
      setForm(null);
      load();
    } catch (err) {
      toast.push(err instanceof Error ? err.message : "Save failed", "error");
    } finally {
      setSaving(false);
    }
  };

  const testConnection = async () => {
    if (!form) return;
    setTesting(true);
    setTestResult(null);
    try {
      const result = await api.testCamera(payload());
      const detail = result.detail as Record<string, unknown>;
      setTestResult(
        result.ok
          ? `Connection OK · ${detail.width}×${detail.height} · ${detail.latency_ms} ms`
          : `Failed: ${detail.error ?? "unknown error"}`,
      );
    } catch (err) {
      setTestResult(err instanceof Error ? err.message : "Test failed");
    } finally {
      setTesting(false);
    }
  };

  const discover = async () => {
    setDiscovering(true);
    try {
      const result = await api.discoverOnvif();
      setDevices(result.detail?.devices || []);
      toast.push(result.message, result.detail?.devices?.length ? "success" : "info");
    } catch (err) {
      toast.push(err instanceof Error ? err.message : "Discovery failed", "error");
    } finally {
      setDiscovering(false);
    }
  };

  const remove = async (camera: Camera) => {
    if (!window.confirm(`Delete camera "${camera.name}"? Recorded events are removed too.`)) return;
    try {
      await api.deleteCamera(camera.id);
      toast.push("Camera deleted", "success");
      load();
    } catch (err) {
      toast.push(err instanceof Error ? err.message : "Delete failed", "error");
    }
  };

  const isSimulated = form?.source_type === "simulated";
  const needsUrl = Boolean(form && form.source_type !== "simulated" && !form.id);

  return (
    <>
      <TopBar title="Cameras" subtitle="CCTV sources, inference settings and stream health">
        {permissions?.can_configure_cameras === true ? (
          <>
            <button className="btn sm" onClick={discover} disabled={discovering}>
              <WarningIcon size={13} /> {discovering ? "Scanning…" : "Discover ONVIF"}
            </button>
            <button className="btn sm primary" onClick={openNew}>
              <PlusIcon size={13} /> Add camera
            </button>
          </>
        ) : null}
        <button className="btn sm ghost icon" onClick={load} aria-label="Refresh">
          <RefreshIcon size={14} />
        </button>
      </TopBar>

      <div className="content">
        <Banner tone="info">
          Credentials are encrypted at rest and never returned by the API. Only the sanitised stream URL
          (without username or password) is ever sent to the browser, and previews are proxied through this
          server — camera URLs never appear in the frontend.
        </Banner>

        {devices.length ? (
          <div className="panel">
            <div className="panel-head">
              <div className="panel-title">ONVIF devices found</div>
              <button className="btn sm ghost" onClick={() => setDevices([])}>
                Dismiss
              </button>
            </div>
            <div className="panel-body table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>IP</th>
                    <th>Endpoint</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {devices.map((device) => (
                    <tr key={device.endpoint}>
                      <td>{device.name}</td>
                      <td className="mono">{device.ip}</td>
                      <td className="mono small">{device.endpoint}</td>
                      <td>
                        <button
                          className="btn sm"
                          onClick={() => {
                            setForm({
                              ...defaultForm(),
                              name: device.name,
                              source_type: "rtsp",
                              url: "",
                            });
                            toast.push("Enter the device's RTSP URL and credentials", "info");
                          }}
                        >
                          Use
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ) : null}

        {loading ? (
          <EmptyState title="Loading cameras…" />
        ) : cameras.length === 0 ? (
          <EmptyState title="No cameras yet" hint="Add an RTSP camera or a simulated demo feed." />
        ) : (
          <div className="camera-grid">
            {cameras.map((camera) => (
              <CameraCard
                key={camera.id}
                camera={{ ...camera, runtime: live.cameras[camera.id] || camera.runtime }}
                canControl={Boolean(permissions?.can_control_cameras)}
              />
            ))}
          </div>
        )}

        {permissions?.can_configure_cameras === true ? (
          <div className="panel">
            <div className="panel-head">
              <div className="panel-title">Configuration</div>
            </div>
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Type</th>
                    <th>Stream</th>
                    <th>Inference</th>
                    <th>Sampling</th>
                    <th>Thresholds</th>
                    <th>Cooldown</th>
                    <th>Enabled</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {cameras.map((camera) => (
                    <tr key={camera.id}>
                      <td>
                        {camera.name}
                        <div className="small muted">{camera.location || "—"}</div>
                        <div className="small muted">Added {formatDateTime(camera.created_at)}</div>
                      </td>
                      <td className="mono">{camera.source_type}</td>
                      <td className="mono small" style={{ maxWidth: 240, wordBreak: "break-all" }}>
                        {camera.url_display || "—"}
                        {camera.has_credentials ? (
                          <div>
                            <StatusPill tone="green">credentials stored</StatusPill>
                          </div>
                        ) : null}
                      </td>
                      <td className="mono">
                        {camera.inference_width}×{camera.inference_height}
                      </td>
                      <td className="mono">1/{camera.frame_sample}</td>
                      <td className="mono small">
                        person {camera.detect_conf.toFixed(2)} · phone {camera.phone_conf.toFixed(2)}
                        <div>persist {camera.persistence_threshold.toFixed(2)}</div>
                      </td>
                      <td className="mono">{camera.alert_cooldown_s}s</td>
                      <td>
                        <StatusPill tone={camera.enabled ? "green" : "gray"}>
                          {camera.enabled ? "enabled" : "disabled"}
                        </StatusPill>
                      </td>
                      <td>
                        <div className="row" style={{ gap: 6 }}>
                          <button className="btn sm" onClick={() => openEdit(camera)}>
                            Edit
                          </button>
                          {permissions?.can_manage_users === true ? (
                            <button className="btn sm danger" onClick={() => remove(camera)}>
                              Delete
                            </button>
                          ) : null}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ) : null}
      </div>

      {form ? (
        <Modal
          title={form.id ? `Edit camera` : "Add camera"}
          onClose={() => setForm(null)}
          wide
          footer={
            <>
              <button className="btn ghost" onClick={() => setForm(null)}>
                Cancel
              </button>
              <button className="btn" onClick={testConnection} disabled={testing || !form.name}>
                {testing ? "Testing…" : "Test connection"}
              </button>
              <button className="btn primary" onClick={save} disabled={saving || !form.name || needsUrl}>
                {saving ? "Saving…" : "Save camera"}
              </button>
            </>
          }
        >
          <div className="stack">
            <div className="field-row">
              <Field label="Camera name">
                <input value={form.name} onChange={(e) => update("name", e.target.value)} />
              </Field>
              <Field label="Location">
                <input value={form.location} onChange={(e) => update("location", e.target.value)} />
              </Field>
            </div>

            <div className="field-row">
              <Field label="Source type">
                <select
                  value={form.source_type}
                  onChange={(e) => update("source_type", e.target.value as Camera["source_type"])}
                >
                  <option value="rtsp">RTSP (CCTV)</option>
                  <option value="http">HTTP / MJPEG</option>
                  <option value="webcam">USB webcam (index)</option>
                  <option value="file">Local video file</option>
                  <option value="simulated">Simulated demo feed</option>
                </select>
              </Field>
              <Field
                label="Stream URL / path"
                hint={
                  isSimulated
                    ? "Not required for the simulated demo feed."
                    : form.id
                      ? "Leave blank to keep the stored URL unchanged."
                      : "Example: rtsp://192.168.1.64:554/Streaming/Channels/101"
                }
              >
                <input
                  value={form.url}
                  onChange={(e) => update("url", e.target.value)}
                  placeholder={isSimulated ? "sim://classroom" : "rtsp://…"}
                  autoComplete="off"
                />
              </Field>
            </div>

            {!isSimulated && form.source_type !== "file" && form.source_type !== "webcam" ? (
              <div className="field-row">
                <Field label="Camera username" hint="Stored encrypted, never shown again.">
                  <input
                    value={form.username}
                    onChange={(e) => update("username", e.target.value)}
                    autoComplete="off"
                  />
                </Field>
                <Field label="Camera password">
                  <input
                    type="password"
                    value={form.password}
                    onChange={(e) => update("password", e.target.value)}
                    autoComplete="new-password"
                  />
                </Field>
              </div>
            ) : null}

            <div className="field-row three">
              <Field label="Inference width" hint="Analysis resolution (not the camera's native size).">
                <input
                  type="number"
                  min={160}
                  max={3840}
                  value={form.inference_width}
                  onChange={(e) => update("inference_width", Number(e.target.value))}
                />
              </Field>
              <Field label="Inference height">
                <input
                  type="number"
                  min={120}
                  max={2160}
                  value={form.inference_height}
                  onChange={(e) => update("inference_height", Number(e.target.value))}
                />
              </Field>
              <Field label="Frame sampling" hint="Process 1 of every N frames.">
                <input
                  type="number"
                  min={1}
                  max={30}
                  value={form.frame_sample}
                  onChange={(e) => update("frame_sample", Number(e.target.value))}
                />
              </Field>
            </div>

            <div className="field-row three">
              <Field label="Person confidence">
                <input
                  type="number"
                  step={0.01}
                  min={0.05}
                  max={0.95}
                  value={form.detect_conf}
                  onChange={(e) => update("detect_conf", Number(e.target.value))}
                />
              </Field>
              <Field label="Phone confidence">
                <input
                  type="number"
                  step={0.01}
                  min={0.05}
                  max={0.95}
                  value={form.phone_conf}
                  onChange={(e) => update("phone_conf", Number(e.target.value))}
                />
              </Field>
              <Field label="Alert persistence" hint="0.65 = conservative, 0.45 = sensitive.">
                <input
                  type="number"
                  step={0.01}
                  min={0.3}
                  max={0.99}
                  value={form.persistence_threshold}
                  onChange={(e) => update("persistence_threshold", Number(e.target.value))}
                />
              </Field>
            </div>

            <div className="field-row">
              <Field label="Alert cooldown (seconds)">
                <input
                  type="number"
                  min={5}
                  max={3600}
                  value={form.alert_cooldown_s}
                  onChange={(e) => update("alert_cooldown_s", Number(e.target.value))}
                />
              </Field>
              <div className="stack" style={{ gap: 10, alignContent: "end" }}>
                <Toggle checked={form.enabled} onChange={(v) => update("enabled", v)} label="Camera enabled" />
                <Toggle
                  checked={form.auto_start}
                  onChange={(v) => update("auto_start", v)}
                  label="Start monitoring on server boot"
                />
                <Toggle
                  checked={form.phone_detection_enabled}
                  onChange={(v) => update("phone_detection_enabled", v)}
                  label="Phone-use detection enabled"
                />
              </div>
            </div>

            {testResult ? (
              <Banner tone={testResult.startsWith("Connection OK") ? "good" : "danger"}>{testResult}</Banner>
            ) : null}
          </div>
        </Modal>
      ) : null}
    </>
  );
}
