import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useAuth } from "../lib/auth";
import { useLive } from "../lib/ws";
import { useVoice } from "../lib/useVoice";
import {
  AlertIcon,
  BellIcon,
  CameraIcon,
  ChartIcon,
  ClockIcon,
  LogoutIcon,
  SettingsIcon,
  ShieldIcon,
  SparkIcon,
} from "./Icons";
import { StatusPill } from "./Primitives";

const NAV = [
  { to: "/monitor", label: "Live monitor", icon: CameraIcon, section: "Operations" },
  { to: "/events", label: "Events", icon: ClockIcon },
  { to: "/cameras", label: "Cameras", icon: SparkIcon },
  { to: "/reports", label: "Reports", icon: ChartIcon, section: "Insights" },
  { to: "/settings", label: "Settings", icon: SettingsIcon, section: "Administration" },
  { to: "/audit", label: "Audit log", icon: ShieldIcon, adminOnly: true },
];

export function Layout() {
  const { user, logout, permissions } = useAuth();
  const live = useLive();
  const voice = useVoice();
  const navigate = useNavigate();
  const alerts = live.alerts.filter((alert) => alert.type === "phone_use_alert").length;

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">
            <AlertIcon size={19} />
          </div>
          <div>
            <div className="brand-title">Smart Classroom AI</div>
            <div className="brand-sub">Monitoring suite</div>
          </div>
        </div>
        <nav className="nav">
          {NAV.filter((item) => !item.adminOnly || permissions?.can_manage_users).map((item) => (
            <div key={item.to}>
              {item.section ? <div className="nav-section">{item.section}</div> : null}
              <NavLink
                to={item.to}
                className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}
              >
                <item.icon size={16} />
                <span>{item.label}</span>
                {item.to === "/events" && alerts > 0 ? (
                  <span className="pill tone-red" style={{ marginLeft: "auto", padding: "1px 6px", fontSize: 10 }}>
                    {alerts}
                  </span>
                ) : null}
              </NavLink>
            </div>
          ))}
        </nav>
        <div className="sidebar-footer">
          <div className="row" style={{ gap: 6 }}>
            <StatusPill tone={live.connected ? "green" : "red"}>
              {live.connected ? "LIVE" : "RECONNECTING"}
            </StatusPill>
            <StatusPill tone={voice.enabled ? "blue" : "gray"} icon={false}>
              <BellIcon size={12} /> VOICE {voice.enabled ? "ON" : "OFF"}
            </StatusPill>
          </div>
          <div className="user-card">
            <div className="avatar">{(user?.username || "?").slice(0, 2).toUpperCase()}</div>
            <div style={{ minWidth: 0, flex: 1 }}>
              <div className="user-name">{user?.full_name || user?.username}</div>
              <div className="user-role">{user?.role}</div>
            </div>
            <button
              className="btn icon ghost sm"
              title="Sign out"
              aria-label="Sign out"
              onClick={() => {
                logout();
                navigate("/login");
              }}
            >
              <LogoutIcon size={14} />
            </button>
          </div>
        </div>
      </aside>
      <main className="main">
        <Outlet />
      </main>
    </div>
  );
}

export function TopBar({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children?: React.ReactNode;
}) {
  const live = useLive();
  return (
    <header className="topbar">
      <div>
        <div className="page-title">{title}</div>
        {subtitle ? <div className="page-sub">{subtitle}</div> : null}
      </div>
      <div className="row" style={{ gap: 10 }}>
        {children}
        <StatusPill tone={live.connected ? "green" : "amber"} title="Dashboard realtime connection">
          {live.connected ? "Realtime" : "Reconnecting…"}
        </StatusPill>
      </div>
    </header>
  );
}
