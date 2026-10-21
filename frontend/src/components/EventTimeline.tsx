import { CATEGORY_META, formatTime, percent, relativeTime } from "../lib/format";
import type { DetectionEvent } from "../lib/types";
import { EmptyState } from "./Primitives";

export function EventTimeline({
  events,
  onSelect,
  limit = 30,
}: {
  events: DetectionEvent[];
  onSelect?: (event: DetectionEvent) => void;
  limit?: number;
}) {
  if (!events.length) {
    return <EmptyState title="No events yet" hint="Detection events appear here as they are recorded." />;
  }
  return (
    <div className="timeline">
      {events.slice(0, limit).map((event) => {
        const meta = CATEGORY_META[event.category] ?? CATEGORY_META.uncertain;
        return (
          <div
            key={event.id}
            className="timeline-item"
            onClick={() => onSelect?.(event)}
            role={onSelect ? "button" : undefined}
            tabIndex={onSelect ? 0 : undefined}
            onKeyDown={(e) => {
              if (onSelect && (e.key === "Enter" || e.key === " ")) onSelect(event);
            }}
          >
            <span className={`bar tone-${meta.tone}`} style={{ background: "currentColor" }} />
            <div style={{ minWidth: 0 }}>
              <div className="timeline-title">
                <span>{meta.label}</span>
                {event.simulated ? (
                  <span className="pill tone-amber" style={{ fontSize: 9.5, padding: "1px 6px" }}>
                    SIMULATED
                  </span>
                ) : null}
                {event.alert_status === "suppressed" ? (
                  <span className="pill tone-gray" style={{ fontSize: 9.5, padding: "1px 6px" }}>
                    COOLDOWN
                  </span>
                ) : null}
                {event.acknowledged_by ? (
                  <span className="pill tone-green" style={{ fontSize: 9.5, padding: "1px 6px" }}>
                    REVIEWED
                  </span>
                ) : null}
              </div>
              <div className="timeline-sub">
                <span>{event.camera_name || `Camera ${event.camera_id}`}</span>
                <span>{event.tracking_id}</span>
                <span>{percent(event.confidence)}</span>
                {event.duration_s > 0 ? <span>{event.duration_s.toFixed(1)}s</span> : null}
              </div>
            </div>
            <div className="timeline-time">
              <div>{formatTime(event.started_at)}</div>
              <div style={{ opacity: 0.7 }}>{relativeTime(event.started_at)}</div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
