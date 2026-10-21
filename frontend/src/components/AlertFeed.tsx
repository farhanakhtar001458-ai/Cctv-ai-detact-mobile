import { formatTime, percent } from "../lib/format";
import type { AlertMessage } from "../lib/types";
import { EmptyState } from "./Primitives";

export function AlertFeed({ alerts, limit = 12 }: { alerts: AlertMessage[]; limit?: number }) {
  if (!alerts.length) {
    return (
      <EmptyState
        title="No alerts in this session"
        hint="Sustained phone-use alerts are listed here. Cooldown-suppressed duplicates are shown separately."
      />
    );
  }
  return (
    <div className="alert-feed">
      {alerts.slice(0, limit).map((alert, index) => {
        const suppressed = alert.type === "alert_suppressed";
        return (
          <div key={`${alert.event_id}-${index}`} className={`alert-card ${suppressed ? "suppressed" : ""}`}>
            <div className="alert-top">
              <strong style={{ fontSize: 12 }}>
                {suppressed ? "Alert suppressed" : "Phone-use alert"}
              </strong>
              <span className="mono small muted">
                {formatTime(alert.timestamp ? alert.timestamp * 1000 : Date.now())}
              </span>
            </div>
            <div className="row small" style={{ gap: 8 }}>
              <span className="mono">{alert.tracking_id}</span>
              <span className="muted">{alert.camera_name}</span>
              {alert.confidence !== undefined ? <span className="mono">{percent(alert.confidence)}</span> : null}
              {alert.simulated ? (
                <span className="pill tone-amber" style={{ fontSize: 9.5, padding: "1px 6px" }}>
                  SIMULATED
                </span>
              ) : null}
            </div>
            <div className="small muted">
              {suppressed
                ? `Not announced: ${alert.reason === "rate_limited" ? "global announcement limit" : "cooldown for this person"}`
                : alert.message || "Announcement queued"}
            </div>
          </div>
        );
      })}
    </div>
  );
}
