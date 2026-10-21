import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { useVoice } from "../lib/useVoice";
import { titleCase } from "../lib/format";
import type { SettingItem, User, VoiceStatus } from "../lib/types";
import { TopBar } from "../components/Layout";
import { SpeakerIcon, WarningIcon } from "../components/Icons";
import {
  Banner,
  EmptyState,
  Field,
  Modal,
  Spinner,
  StatusPill,
  Toggle,
  useToast,
} from "../components/Primitives";

const CATEGORY_LABELS: Record<string, string> = {
  detection: "Detection & alerting",
  voice: "Voice announcements",
  privacy: "Privacy & retention",
  performance: "Performance",
};

function SettingRow({
  item,
  disabled,
  onChange,
}: {
  item: SettingItem;
  disabled: boolean;
  onChange: (key: string, value: unknown) => void;
}) {
  const value = item.value;
  if (typeof item.default === "boolean") {
    return (
      <div className="row between" style={{ padding: "9px 0", borderBottom: "1px solid #17212d" }}>
        <div>
          <div style={{ fontSize: 12.5, fontWeight: 600 }}>{item.label}</div>
          {item.description ? <div className="small muted">{item.description}</div> : null}
        </div>
        <Toggle
          checked={Boolean(value)}
          label=""
          disabled={disabled}
          onChange={(next) => onChange(item.key, next)}
        />
      </div>
    );
  }
  if (item.key === "privacy_notice") {
    return (
      <div style={{ padding: "9px 0", borderBottom: "1px solid #17212d" }}>
        <Field label={item.label} hint={item.description}>
          <textarea
            rows={4}
            value={String(value ?? "")}
            disabled={disabled}
            onChange={(e) => onChange(item.key, e.target.value)}
          />
        </Field>
      </div>
    );
  }
  if (item.key === "voice_output") {
    return (
      <div style={{ padding: "9px 0", borderBottom: "1px solid #17212d" }}>
        <Field label={item.label} hint={item.description}>
          <select value={String(value)} disabled={disabled} onChange={(e) => onChange(item.key, e.target.value)}>
            <option value="dashboard">Dashboard browser (monitoring PC)</option>
            <option value="server">Server audio device</option>
            <option value="both">Both</option>
          </select>
        </Field>
      </div>
    );
  }
  const numeric = typeof item.default === "number";
  return (
    <div style={{ padding: "9px 0", borderBottom: "1px solid #17212d" }}>
      <Field label={item.label} hint={item.description}>
        <input
          type={numeric ? "number" : "text"}
          step={numeric ? "0.01" : undefined}
          value={String(value ?? "")}
          disabled={disabled}
          onChange={(e) => onChange(item.key, numeric ? Number(e.target.value) : e.target.value)}
        />
      </Field>
    </div>
  );
}

export function SettingsPage() {
  const { permissions, user, refresh } = useAuth();
  const voice = useVoice();
  const toast = useToast();
  const [items, setItems] = useState<SettingItem[]>([]);
  const [draft, setDraft] = useState<Record<string, unknown>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [voiceStatus, setVoiceStatus] = useState<VoiceStatus | null>(null);
  const [modelInfo, setModelInfo] = useState<Record<string, unknown> | null>(null);
  const [users, setUsers] = useState<User[]>([]);
  const [userModal, setUserModal] = useState(false);
  const [newUser, setNewUser] = useState({ username: "", password: "", full_name: "", role: "viewer" });
  const [pwdModal, setPwdModal] = useState(false);
  const [pwd, setPwd] = useState({ current: "", next: "" });

  const canEdit = !!permissions?.can_change_settings;

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [settings, voiceInfo, model] = await Promise.all([
        api.settings(),
        api.voiceStatus().catch(() => null),
        api.modelStatus().catch(() => null),
      ]);
      setItems(settings);
      setDraft(Object.fromEntries(settings.map((item) => [item.key, item.value])));
      setVoiceStatus(voiceInfo as VoiceStatus | null);
      setModelInfo(model);
      if (permissions?.can_manage_users) {
        setUsers(await api.users().catch(() => []));
      }
    } catch (err) {
      toast.push(err instanceof Error ? err.message : "Could not load settings", "error");
    } finally {
      setLoading(false);
    }
  }, [permissions?.can_manage_users, toast]);

  useEffect(() => {
    load();
  }, [load]);

  const dirty = useMemo(
    () => items.some((item) => JSON.stringify(draft[item.key]) !== JSON.stringify(item.value)),
    [items, draft],
  );

  const save = async () => {
    setSaving(true);
    try {
      const changed = Object.fromEntries(
        items
          .filter((item) => JSON.stringify(draft[item.key]) !== JSON.stringify(item.value))
          .map((item) => [item.key, draft[item.key]]),
      );
      if (Object.keys(changed).length) await api.updateSettings(changed);
      toast.push("Settings saved", "success");
      await load();
    } catch (err) {
      toast.push(err instanceof Error ? err.message : "Save failed", "error");
    } finally {
      setSaving(false);
    }
  };

  const reset = async () => {
    if (!window.confirm("Restore all runtime settings to their defaults?")) return;
    try {
      await api.resetSettings();
      toast.push("Settings restored", "success");
      load();
    } catch (err) {
      toast.push(err instanceof Error ? err.message : "Reset failed", "error");
    }
  };

  const reloadModel = async () => {
    try {
      const result = await api.reloadModel();
      toast.push(result.message, "success");
      load();
    } catch (err) {
      toast.push(err instanceof Error ? err.message : "Reload failed", "error");
    }
  };

  const createUser = async () => {
    try {
      await api.createUser(newUser);
      toast.push("User created", "success");
      setUserModal(false);
      setNewUser({ username: "", password: "", full_name: "", role: "viewer" });
      load();
    } catch (err) {
      toast.push(err instanceof Error ? err.message : "Could not create user", "error");
    }
  };

  const changePassword = async () => {
    try {
      await api.changePassword(pwd.current, pwd.next);
      toast.push("Password updated", "success");
      setPwdModal(false);
      setPwd({ current: "", next: "" });
    } catch (err) {
      toast.push(err instanceof Error ? err.message : "Could not change password", "error");
    }
  };

  const grouped = useMemo(() => {
    const map: Record<string, SettingItem[]> = {};
    items.forEach((item) => {
      map[item.category] = map[item.category] || [];
      map[item.category].push(item);
    });
    return map;
  }, [items]);

  return (
    <>
      <TopBar
        title="Settings"
        subtitle={`Runtime configuration · signed in as ${user?.username} (${user?.role})`}
      >
        {canEdit ? (
          <>
            <button className="btn sm ghost" onClick={reset}>
              Restore defaults
            </button>
            <button className="btn sm primary" onClick={save} disabled={!dirty || saving}>
              {saving ? "Saving…" : dirty ? "Save changes" : "Saved"}
            </button>
          </>
        ) : (
          <StatusPill tone="gray">Read-only for your role</StatusPill>
        )}
      </TopBar>

      <div className="content">
        {!canEdit ? (
          <Banner tone="info">Only administrators can change runtime settings. Values shown are current.</Banner>
        ) : null}
        {loading ? <Spinner label="Loading settings…" /> : null}

        <div className="grid cols-2">
          <div className="panel">
            <div className="panel-head">
              <div className="panel-title">Voice announcements</div>
              <StatusPill tone={voice.enabled ? "blue" : "gray"}>
                browser {voice.enabled ? "on" : "off"}
              </StatusPill>
            </div>
            <div className="panel-body stack">
              <div className="row" style={{ gap: 10 }}>
                <Toggle checked={voice.enabled} onChange={voice.setEnabled} label="Play announcements here" />
                <button className="btn sm" onClick={voice.test} disabled={!voice.supported}>
                  <SpeakerIcon size={13} /> Test browser audio
                </button>
                <button
                  className="btn sm"
                  onClick={async () => {
                    try {
                      await api.testVoice();
                      toast.push("Server announcement queued", "success");
                    } catch (err) {
                      toast.push(err instanceof Error ? err.message : "Failed", "error");
                    }
                  }}
                >
                  Test backend path
                </button>
              </div>
              <div className="field-row">
                <Field label={`Speaking rate ${voice.rate.toFixed(2)}×`}>
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
              <div className="small muted">
                Server engine:{" "}
                <span className="mono">{voiceStatus?.engine.engine ?? "unknown"}</span>
                {voiceStatus?.engine.available
                  ? " — available for speaker-system output"
                  : " — no local TTS engine; use the dashboard output (browser speech)"}
              </div>
              {voiceStatus?.last_announcement ? (
                <div className="small">
                  Last backend announcement: <span className="mono">{voiceStatus.last_announcement.text}</span>{" "}
                  <span className="muted">({voiceStatus.last_announcement.camera_name})</span>
                </div>
              ) : (
                <div className="small muted">No backend announcements yet.</div>
              )}
              <Banner tone="info" icon={false}>
                Audio is never sent to cameras or classroom speakers unless you explicitly select the
                "server audio device" output and route it to an authorised system.
              </Banner>
            </div>
          </div>

          <div className="panel">
            <div className="panel-head">
              <div className="panel-title">Detector & model</div>
              <button className="btn sm" onClick={reloadModel} disabled={!canEdit}>
                Reload
              </button>
            </div>
            <div className="panel-body stack">
              {modelInfo ? (
                <>
                  <div className="row" style={{ gap: 8 }}>
                    <StatusPill tone={modelInfo.loaded ? "green" : "red"}>
                      {modelInfo.loaded ? "LOADED" : "NOT LOADED"}
                    </StatusPill>
                    <StatusPill tone={modelInfo.degraded ? "amber" : "blue"}>
                      {String(modelInfo.backend)}
                    </StatusPill>
                    <StatusPill tone={modelInfo.phone_capable ? "blue" : "gray"}>
                      {modelInfo.phone_capable ? "phone-capable" : "no phone class"}
                    </StatusPill>
                  </div>
                  <dl className="kv">
                    <dt>Model</dt>
                    <dd>{String(modelInfo.model || "—")}</dd>
                    <dt>Device</dt>
                    <dd>{String(modelInfo.device || "—")}</dd>
                    <dt>Warm-up</dt>
                    <dd>{Number(modelInfo.warmup_ms || 0).toFixed(0)} ms</dd>
                    <dt>Classes</dt>
                    <dd>{String(modelInfo.classes ?? "—")}</dd>
                  </dl>
                  {modelInfo.degraded ? (
                    <Banner tone="warn">
                      {String(modelInfo.degraded_reason || "Running in degraded mode.")} Provide YOLO weights
                      and press Reload, or set <span className="mono">SCAI_DETECTOR_BACKEND=onnx</span> with an
                      exported model.
                    </Banner>
                  ) : null}
                  {modelInfo.error ? <Banner tone="danger">{String(modelInfo.error)}</Banner> : null}
                </>
              ) : (
                <div className="small muted">Model status unavailable.</div>
              )}
            </div>
          </div>
        </div>

        {Object.entries(grouped).map(([category, categoryItems]) => (
          <div className="panel" key={category}>
            <div className="panel-head">
              <div className="panel-title">{CATEGORY_LABELS[category] ?? titleCase(category)}</div>
              {category === "privacy" ? <StatusPill tone="green">privacy-by-design</StatusPill> : null}
            </div>
            <div className="panel-body" style={{ paddingTop: 4 }}>
              {categoryItems.map((item) => (
                <SettingRow
                  key={item.key}
                  item={{ ...item, value: draft[item.key] ?? item.value }}
                  disabled={!canEdit}
                  onChange={(key, value) => setDraft((prev) => ({ ...prev, [key]: value }))}
                />
              ))}
            </div>
          </div>
        ))}

        {permissions?.can_manage_users ? (
          <div className="panel">
            <div className="panel-head">
              <div className="panel-title">Staff accounts</div>
              <button className="btn sm" onClick={() => setUserModal(true)}>
                Add user
              </button>
            </div>
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th>Username</th>
                    <th>Name</th>
                    <th>Role</th>
                    <th>Active</th>
                    <th>Last login</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {users.map((entry) => (
                    <tr key={entry.id}>
                      <td className="mono">{entry.username}</td>
                      <td>{entry.full_name || "—"}</td>
                      <td>
                        <StatusPill tone={entry.role === "admin" ? "purple" : entry.role === "supervisor" ? "blue" : "gray"}>
                          {entry.role}
                        </StatusPill>
                      </td>
                      <td>
                        <Toggle
                          checked={entry.is_active}
                          label=""
                          disabled={entry.username === user?.username}
                          onChange={async (next) => {
                            try {
                              await api.updateUser(entry.id, { is_active: next });
                              load();
                            } catch (err) {
                              toast.push(err instanceof Error ? err.message : "Failed", "error");
                            }
                          }}
                        />
                      </td>
                      <td className="small">{entry.last_login_at ? new Date(entry.last_login_at).toLocaleString() : "never"}</td>
                      <td>
                        <select
                          value={entry.role}
                          disabled={entry.username === user?.username}
                          onChange={async (e) => {
                            try {
                              await api.updateUser(entry.id, { role: e.target.value });
                              toast.push("Role updated", "success");
                              load();
                              refresh();
                            } catch (err) {
                              toast.push(err instanceof Error ? err.message : "Failed", "error");
                            }
                          }}
                          style={{ width: 130 }}
                        >
                          <option value="viewer">viewer</option>
                          <option value="supervisor">supervisor</option>
                          <option value="admin">admin</option>
                        </select>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="panel-body row" style={{ borderTop: "1px solid var(--line)" }}>
              <button className="btn sm" onClick={() => setPwdModal(true)}>
                Change my password
              </button>
              <span className="small muted">
                Roles: viewer (read-only) · supervisor (cameras, review, export) · admin (all settings and
                accounts)
              </span>
            </div>
          </div>
        ) : (
          <div className="panel">
            <div className="panel-head">
              <div className="panel-title">Account</div>
            </div>
            <div className="panel-body">
              <button className="btn sm" onClick={() => setPwdModal(true)}>
                Change my password
              </button>
            </div>
          </div>
        )}

        {items.length === 0 && !loading ? <EmptyState title="No settings available" /> : null}
      </div>

      {userModal ? (
        <Modal
          title="Add staff account"
          onClose={() => setUserModal(false)}
          footer={
            <>
              <button className="btn ghost" onClick={() => setUserModal(false)}>
                Cancel
              </button>
              <button
                className="btn primary"
                onClick={createUser}
                disabled={newUser.username.length < 3 || newUser.password.length < 8}
              >
                Create
              </button>
            </>
          }
        >
          <div className="stack">
            <div className="field-row">
              <Field label="Username">
                <input
                  value={newUser.username}
                  onChange={(e) => setNewUser({ ...newUser, username: e.target.value })}
                />
              </Field>
              <Field label="Full name">
                <input
                  value={newUser.full_name}
                  onChange={(e) => setNewUser({ ...newUser, full_name: e.target.value })}
                />
              </Field>
            </div>
            <Field label="Temporary password" hint="At least 8 characters; the user should change it after first login.">
              <input
                type="password"
                value={newUser.password}
                onChange={(e) => setNewUser({ ...newUser, password: e.target.value })}
              />
            </Field>
            <Field label="Role">
              <select value={newUser.role} onChange={(e) => setNewUser({ ...newUser, role: e.target.value })}>
                <option value="viewer">viewer</option>
                <option value="supervisor">supervisor</option>
                <option value="admin">admin</option>
              </select>
            </Field>
            <Banner tone="warn" icon={false}>
              <WarningIcon size={13} /> Staff accounts grant access to monitoring data. Follow your
              institution's authorisation process before creating accounts.
            </Banner>
          </div>
        </Modal>
      ) : null}

      {pwdModal ? (
        <Modal
          title="Change password"
          onClose={() => setPwdModal(false)}
          footer={
            <>
              <button className="btn ghost" onClick={() => setPwdModal(false)}>
                Cancel
              </button>
              <button className="btn primary" onClick={changePassword} disabled={pwd.next.length < 8}>
                Update
              </button>
            </>
          }
        >
          <div className="stack">
            <Field label="Current password">
              <input type="password" value={pwd.current} onChange={(e) => setPwd({ ...pwd, current: e.target.value })} />
            </Field>
            <Field label="New password" hint="Minimum 8 characters.">
              <input type="password" value={pwd.next} onChange={(e) => setPwd({ ...pwd, next: e.target.value })} />
            </Field>
          </div>
        </Modal>
      ) : null}
    </>
  );
}
