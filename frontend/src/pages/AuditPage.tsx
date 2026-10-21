import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { formatDateTime } from "../lib/format";
import type { AuditEntry } from "../lib/types";
import { TopBar } from "../components/Layout";
import { RefreshIcon } from "../components/Icons";
import { Banner, EmptyState, Spinner, StatusPill, Field } from "../components/Primitives";

export function AuditPage() {
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");

  const load = async () => {
    setLoading(true);
    try {
      setEntries(await api.audit(300));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load audit log");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const filtered = entries.filter((entry) =>
    filter
      ? `${entry.actor} ${entry.action} ${entry.target} ${entry.detail}`.toLowerCase().includes(filter.toLowerCase())
      : true,
  );

  return (
    <>
      <TopBar title="Audit log" subtitle="Administrative and security-relevant actions">
        <button className="btn sm" onClick={load} disabled={loading}>
          <RefreshIcon size={13} /> Refresh
        </button>
      </TopBar>
      <div className="content">
        <Banner tone="info">
          Logins, camera and settings changes, exports, reviews and model reloads are recorded here with the
          acting user and source IP. A client-supplied <span className="mono">Cookie: token=…</span> never
          appears in stored strings (credential redaction is applied).
        </Banner>
        {error ? <Banner tone="danger">{error}</Banner> : null}
        <div className="panel">
          <div className="panel-head">
            <div className="panel-title">Recent actions</div>
            <div style={{ width: 260 }}>
              <Field label="">
                <input
                  type="search"
                  placeholder="Search actor, action, target…"
                  value={filter}
                  onChange={(e) => setFilter(e.target.value)}
                />
              </Field>
            </div>
          </div>
          {loading ? (
            <div className="panel-body">
              <Spinner />
            </div>
          ) : filtered.length === 0 ? (
            <EmptyState title="No audit entries" />
          ) : (
            <div className="table-wrap" style={{ maxHeight: "72vh", overflowY: "auto" }}>
              <table className="data">
                <thead>
                  <tr>
                    <th>Time</th>
                    <th>Actor</th>
                    <th>Action</th>
                    <th>Target</th>
                    <th>Detail</th>
                    <th>IP</th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((entry) => (
                    <tr key={entry.id}>
                      <td className="mono">{formatDateTime(entry.created_at)}</td>
                      <td className="mono">{entry.actor}</td>
                      <td>
                        <StatusPill
                          tone={
                            entry.action.includes("fail") || entry.action.includes("delete")
                              ? "red"
                              : entry.action.includes("login")
                                ? "blue"
                                : "gray"
                          }
                        >
                          {entry.action}
                        </StatusPill>
                      </td>
                      <td className="mono small">{entry.target || "—"}</td>
                      <td className="small" style={{ maxWidth: 420, wordBreak: "break-word" }}>
                        {entry.detail || "—"}
                      </td>
                      <td className="mono small">{entry.ip_address || "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </>
  );
}
