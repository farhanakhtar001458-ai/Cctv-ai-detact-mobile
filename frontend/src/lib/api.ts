import type {
  AuditEntry,
  Camera,
  CameraSummary,
  DailySummary,
  DetectionEvent,
  Permissions,
  SettingItem,
  StatsOverview,
  User,
  VoiceStatus,
} from "./types";

const TOKEN_KEY = "scai.token";
const USER_KEY = "scai.user";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function getStoredUser(): User | null {
  const raw = localStorage.getItem(USER_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as User;
  } catch {
    return null;
  }
}

export function setSession(token: string, user: User): void {
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(USER_KEY, JSON.stringify(user));
}

export function clearSession(): void {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
  window.dispatchEvent(new Event("scai:unauthorized"));
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {
    ...(options.headers as Record<string, string> | undefined),
  };
  if (token) headers.Authorization = `Bearer ${token}`;
  if (options.body && !(options.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
  }
  const response = await fetch(path, { ...options, headers });
  if (response.status === 401) {
    clearSession();
  }
  if (!response.ok) {
    let detail = response.statusText || "Request failed";
    try {
      const data = await response.json();
      if (typeof data.detail === "string") detail = data.detail;
      else if (data.detail) detail = JSON.stringify(data.detail);
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(response.status, detail);
  }
  if (response.status === 204) return undefined as T;
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) return (await response.json()) as T;
  return (await response.text()) as unknown as T;
}

export const api = {
  // auth
  async login(username: string, password: string) {
    const data = await request<{ access_token: string; user: User }>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });
    setSession(data.access_token, data.user);
    return data.user;
  },
  me: () => request<User>("/api/auth/me"),
  permissions: () => request<Permissions>("/api/auth/permissions"),
  changePassword: (current_password: string, new_password: string) =>
    request<{ message: string }>("/api/auth/change-password", {
      method: "POST",
      body: JSON.stringify({ current_password, new_password }),
    }),
  streamToken: () =>
    request<{ token: string; expires_at: string }>("/api/auth/stream-token", { method: "POST" }),

  // cameras
  cameras: () => request<Camera[]>("/api/cameras"),
  createCamera: (payload: Record<string, unknown>) =>
    request<Camera>("/api/cameras", { method: "POST", body: JSON.stringify(payload) }),
  updateCamera: (id: number, payload: Record<string, unknown>) =>
    request<Camera>(`/api/cameras/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
  deleteCamera: (id: number) =>
    request<{ message: string }>(`/api/cameras/${id}`, { method: "DELETE" }),
  startCamera: (id: number) =>
    request<{ message: string }>(`/api/cameras/${id}/start`, { method: "POST" }),
  stopCamera: (id: number) =>
    request<{ message: string }>(`/api/cameras/${id}/stop`, { method: "POST" }),
  testCamera: (payload: Record<string, unknown>) =>
    request<{ ok: boolean; message: string; detail: Record<string, unknown> }>("/api/cameras/test", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  discoverOnvif: () =>
    request<{ ok: boolean; message: string; detail: { devices: Array<Record<string, string>> } }>(
      "/api/cameras/discover/onvif",
      { method: "POST" },
    ),

  // events
  events: (params: Record<string, string | number | undefined>) => {
    const query = new URLSearchParams();
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== "") query.set(key, String(value));
    });
    return request<DetectionEvent[]>(`/api/events?${query.toString()}`);
  },
  acknowledge: (id: number, note: string) =>
    request<{ message: string }>(`/api/events/${id}/acknowledge`, {
      method: "POST",
      body: JSON.stringify({ note }),
    }),

  // stats
  overview: () => request<StatsOverview>("/api/stats/overview"),
  daily: (days: number) => request<DailySummary[]>(`/api/stats/daily?days=${days}`),
  cameraSummary: (days: number) => request<CameraSummary[]>(`/api/stats/cameras?days=${days}`),

  // settings
  settings: () => request<SettingItem[]>("/api/settings"),
  updateSettings: (values: Record<string, unknown>) =>
    request<{ message: string }>("/api/settings", { method: "PUT", body: JSON.stringify({ values }) }),
  resetSettings: () => request<{ message: string }>("/api/settings/reset", { method: "POST" }),
  voiceStatus: () => request<VoiceStatus>("/api/settings/voice/status"),
  testVoice: () => request<{ message: string }>("/api/settings/voice/test", { method: "POST" }),
  modelStatus: () => request<Record<string, unknown>>("/api/settings/model"),
  reloadModel: () => request<{ message: string }>("/api/settings/model/reload", { method: "POST" }),

  // system
  systemInfo: () => request<Record<string, unknown>>("/api/system/info"),
  audit: (limit = 100) => request<AuditEntry[]>(`/api/audit?limit=${limit}`),
  users: () => request<User[]>("/api/auth/users"),
  createUser: (payload: Record<string, unknown>) =>
    request<User>("/api/auth/users", { method: "POST", body: JSON.stringify(payload) }),
  updateUser: (id: number, payload: Record<string, unknown>) =>
    request<User>(`/api/auth/users/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
};

/** Download a CSV export with the auth header applied (blob download). */
export async function downloadCsv(path: string, filename: string): Promise<void> {
  const token = getToken();
  const response = await fetch(path, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  if (!response.ok) throw new ApiError(response.status, "Export failed");
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}
