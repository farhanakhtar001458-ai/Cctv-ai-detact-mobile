import type { EventCategory, TrackState } from "./types";

export function formatTime(iso: string | number | null | undefined): string {
  if (!iso) return "—";
  const date = typeof iso === "number" ? new Date(iso * 1000) : new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString([], {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function relativeTime(iso: string | number | null | undefined): string {
  if (!iso) return "—";
  const date = typeof iso === "number" ? new Date(iso * 1000) : new Date(iso);
  const diff = (Date.now() - date.getTime()) / 1000;
  if (Number.isNaN(diff)) return "—";
  if (diff < 5) return "just now";
  if (diff < 60) return `${Math.round(diff)}s ago`;
  if (diff < 3600) return `${Math.round(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.round(diff / 3600)}h ago`;
  return `${Math.round(diff / 86400)}d ago`;
}

export interface StateMeta {
  label: string;
  short: string;
  tone: "green" | "blue" | "amber" | "red" | "gray";
  description: string;
}

export const STATE_META: Record<TrackState, StateMeta> = {
  none: {
    label: "Person detected",
    short: "PERSON",
    tone: "green",
    description: "A person is visible. No mobile phone is associated with them.",
  },
  phone_detected: {
    label: "Mobile phone detected",
    short: "PHONE",
    tone: "blue",
    description: "A mobile phone is visible near this person but use is not established.",
  },
  possible_phone_use: {
    label: "Possible phone use",
    short: "POSSIBLE",
    tone: "amber",
    description: "Evidence of holding/looking at a phone is accumulating below the alert threshold.",
  },
  sustained_phone_use: {
    label: "Sustained phone-use alert",
    short: "ALERT",
    tone: "red",
    description: "Confidence and persistence thresholds are both satisfied.",
  },
  uncertain: {
    label: "Uncertain detection",
    short: "UNCERTAIN",
    tone: "gray",
    description: "Ambiguous assignment (e.g. two nearby students) or heavy occlusion; needs review.",
  },
};

export const CATEGORY_META: Record<EventCategory, { label: string; tone: StateMeta["tone"] }> = {
  person_detected: { label: "Person detected", tone: "green" },
  phone_detected: { label: "Mobile phone detected", tone: "blue" },
  possible_phone_use: { label: "Possible phone use", tone: "amber" },
  sustained_phone_use: { label: "Sustained phone-use alert", tone: "red" },
  uncertain: { label: "Uncertain detection", tone: "gray" },
};

export function percent(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${Math.round(value * 100)}%`;
}

export function numberOrDash(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return value.toFixed(digits);
}

export function titleCase(value: string): string {
  return value
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase())
    .trim();
}
