import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../lib/auth";
import { AlertIcon, ShieldIcon } from "../components/Icons";
import { Banner, Field } from "../components/Primitives";

export function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(username.trim(), password);
      navigate("/monitor", { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-wrap">
      <form className="login-card" onSubmit={submit}>
        <div className="row" style={{ gap: 10 }}>
          <div className="brand-mark">
            <AlertIcon size={20} />
          </div>
          <div>
            <div className="login-title">Smart Classroom AI</div>
            <div className="brand-sub">Authorised staff only</div>
          </div>
        </div>

        <div className="login-sub">
          Classroom monitoring for mobile-phone use and occupancy. Anonymous tracking IDs only —
          no facial recognition, no biometric profiles.
        </div>

        {error ? <Banner tone="danger">{error}</Banner> : null}

        <Field label="Username">
          <input
            type="text"
            autoComplete="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
            autoFocus
          />
        </Field>
        <Field label="Password">
          <input
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </Field>

        <button className="btn primary" type="submit" disabled={busy || !username || !password}>
          {busy ? "Signing in…" : "Sign in"}
        </button>

        <div className="row small muted" style={{ gap: 8 }}>
          <ShieldIcon size={14} />
          <span>
            Sessions expire automatically. Access and administrative actions are logged for audit.
          </span>
        </div>
      </form>
    </div>
  );
}
