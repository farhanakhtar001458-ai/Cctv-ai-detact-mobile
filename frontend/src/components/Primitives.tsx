import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { AlertIcon, CheckIcon, CloseIcon, WarningIcon } from "./Icons";
import type { StateMeta } from "../lib/format";

/* ------------------------------------------------------------------ pills */

const TONE_ICONS: Record<PillTone, ReactNode> = {
  green: <span aria-hidden>●</span>,
  blue: <span aria-hidden>◆</span>,
  amber: <span aria-hidden>▲</span>,
  red: <AlertIcon size={12} />,
  gray: <span aria-hidden>?</span>,
  purple: <span aria-hidden>★</span>,
};

type PillTone = StateMeta["tone"] | "purple";

export function StatusPill({
  tone,
  children,
  icon = true,
  title,
}: {
  tone: PillTone;
  children: ReactNode;
  icon?: boolean;
  title?: string;
}) {
  return (
    <span className={`pill tone-${tone}`} title={title}>
      {icon ? TONE_ICONS[tone] : null}
      {children}
    </span>
  );
}

/* ----------------------------------------------------------------- toasts */

type ToastTone = "info" | "success" | "error";
interface ToastItem {
  id: number;
  tone: ToastTone;
  message: string;
}

interface ToastContextValue {
  push: (message: string, tone?: ToastTone) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);

  const push = useCallback((message: string, tone: ToastTone = "info") => {
    const id = Date.now() + Math.random();
    setItems((prev) => [...prev, { id, tone, message }]);
    window.setTimeout(() => {
      setItems((prev) => prev.filter((item) => item.id !== id));
    }, 5200);
  }, []);

  const value = useMemo(() => ({ push }), [push]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="toast-stack" role="status" aria-live="polite">
        {items.map((item) => (
          <div key={item.id} className={`toast ${item.tone}`}>
            {item.tone === "error" ? <WarningIcon size={16} /> : <CheckIcon size={16} />}
            <span style={{ flex: 1 }}>{item.message}</span>
            <button
              className="btn icon ghost sm"
              aria-label="Dismiss"
              onClick={() => setItems((prev) => prev.filter((entry) => entry.id !== item.id))}
            >
              <CloseIcon size={12} />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastContextValue {
  const context = useContext(ToastContext);
  if (!context) return { push: () => undefined };
  return context;
}

/* ------------------------------------------------------------------ modal */

export function Modal({
  title,
  children,
  onClose,
  footer,
  wide = false,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  footer?: ReactNode;
  wide?: boolean;
}) {
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        style={wide ? { width: "min(980px, 100%)" } : undefined}
      >
        <div className="modal-head">
          <div className="panel-title">{title}</div>
          <button className="btn icon ghost sm" onClick={onClose} aria-label="Close">
            <CloseIcon size={14} />
          </button>
        </div>
        <div className="modal-body">{children}</div>
        {footer ? <div className="modal-foot">{footer}</div> : null}
      </div>
    </div>
  );
}

/* ----------------------------------------------------------------- inputs */

export function Toggle({
  checked,
  onChange,
  label,
  disabled,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
  label: string;
  disabled?: boolean;
}) {
  return (
    <label className="toggle" style={disabled ? { opacity: 0.5 } : undefined}>
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
      />
      <span className="track" aria-hidden />
      <span>{label}</span>
    </label>
  );
}

export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
      {hint ? <span className="hint">{hint}</span> : null}
    </label>
  );
}

export function Banner({
  tone,
  children,
  icon = true,
}: {
  tone: "warn" | "danger" | "info" | "good";
  children: ReactNode;
  icon?: boolean;
}) {
  return (
    <div className={`banner ${tone}`} role={tone === "danger" ? "alert" : "note"}>
      {icon ? (tone === "danger" || tone === "warn" ? <WarningIcon size={16} /> : <CheckIcon size={16} />) : null}
      <div style={{ flex: 1 }}>{children}</div>
    </div>
  );
}

export function EmptyState({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="empty">
      <div style={{ fontWeight: 600, color: "var(--text-dim)" }}>{title}</div>
      {hint ? <div style={{ marginTop: 4 }}>{hint}</div> : null}
    </div>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="row" style={{ gap: 8, color: "var(--muted)", fontSize: 12 }}>
      <span
        style={{
          width: 13,
          height: 13,
          borderRadius: "50%",
          border: "2px solid #2b3a4c",
          borderTopColor: "var(--blue)",
          display: "inline-block",
          animation: "spin 0.8s linear infinite",
        }}
      />
      {label ?? "Loading…"}
      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </div>
  );
}
