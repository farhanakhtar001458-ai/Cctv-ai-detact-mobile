import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { api, clearSession, getStoredUser, getToken } from "./api";
import type { Permissions, User } from "./types";

interface AuthContextValue {
  user: User | null;
  permissions: Permissions | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(() => (getToken() ? getStoredUser() : null));
  const [permissions, setPermissions] = useState<Permissions | null>(null);
  const [loading, setLoading] = useState<boolean>(Boolean(getToken()));

  const refresh = useCallback(async () => {
    if (!getToken()) {
      setUser(null);
      setPermissions(null);
      setLoading(false);
      return;
    }
    try {
      const [me, perms] = await Promise.all([api.me(), api.permissions()]);
      setUser(me);
      setPermissions(perms);
    } catch {
      setUser(null);
      setPermissions(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    const handler = () => {
      setUser(null);
      setPermissions(null);
    };
    window.addEventListener("scai:unauthorized", handler);
    return () => window.removeEventListener("scai:unauthorized", handler);
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    const loggedIn = await api.login(username, password);
    setUser(loggedIn);
    setLoading(false);
    try {
      setPermissions(await api.permissions());
    } catch {
      setPermissions(null);
    }
  }, []);

  const logout = useCallback(() => {
    clearSession();
    setUser(null);
    setPermissions(null);
  }, []);

  const value = useMemo(
    () => ({ user, permissions, loading, login, logout, refresh }),
    [user, permissions, loading, login, logout, refresh],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside AuthProvider");
  return context;
}
