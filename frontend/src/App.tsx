import { Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider, useAuth } from "./lib/auth";
import { LiveProvider } from "./lib/ws";
import { ToastProvider, Spinner } from "./components/Primitives";
import { Layout } from "./components/Layout";
import { LoginPage } from "./pages/LoginPage";
import { MonitorPage } from "./pages/MonitorPage";
import { EventsPage } from "./pages/EventsPage";
import { CamerasPage } from "./pages/CamerasPage";
import { ReportsPage } from "./pages/ReportsPage";
import { SettingsPage } from "./pages/SettingsPage";
import { AuditPage } from "./pages/AuditPage";

function Protected({ children }: { children: JSX.Element }) {
  const { user, loading } = useAuth();
  if (loading) {
    return (
      <div className="login-wrap">
        <Spinner label="Checking session…" />
      </div>
    );
  }
  if (!user) return <Navigate to="/login" replace />;
  return children;
}

function AdminOnly({ children }: { children: JSX.Element }) {
  const { permissions } = useAuth();
  if (permissions && !permissions.can_manage_users) return <Navigate to="/monitor" replace />;
  return children;
}

export default function App() {
  return (
    <AuthProvider>
      <ToastProvider>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route
            path="/"
            element={
              <Protected>
                <LiveProvider>
                  <Layout />
                </LiveProvider>
              </Protected>
            }
          >
            <Route index element={<Navigate to="/monitor" replace />} />
            <Route path="monitor" element={<MonitorPage />} />
            <Route path="events" element={<EventsPage />} />
            <Route path="cameras" element={<CamerasPage />} />
            <Route path="reports" element={<ReportsPage />} />
            <Route path="settings" element={<SettingsPage />} />
            <Route
              path="audit"
              element={
                <AdminOnly>
                  <AuditPage />
                </AdminOnly>
              }
            />
          </Route>
          <Route path="*" element={<Navigate to="/monitor" replace />} />
        </Routes>
      </ToastProvider>
    </AuthProvider>
  );
}
