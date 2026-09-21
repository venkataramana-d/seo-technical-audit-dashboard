"use client";

// Client-side auth state for the app. Talks to the Python api/auth.py handler
// (signup/login/logout/me), which sets an HttpOnly `sa_session` cookie - so the
// browser never sees the token; we only ever learn "who am I" from GET /api/auth.
//
// Note on local `next dev`: the Python api/*.py functions only run under Vercel's
// runtime, so GET /api/auth 404s locally. We treat an unreachable auth backend as
// status "unavailable" and DO NOT gate the app (so frontend-only dev still works);
// on a real deploy the endpoint returns {user: null|<user>} and the gate applies.

import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";

export type AuthRole = "admin" | "user";
export type AuthUser = { id: number; email: string; orgId: number | null; role: AuthRole | null };
export type AuthStatus = "loading" | "authed" | "anon" | "unavailable";

type AuthContextValue = {
  user: AuthUser | null;
  status: AuthStatus;
  refresh: () => Promise<void>;
  login: (email: string, password: string) => Promise<void>;
  signup: (email: string, password: string, orgName: string) => Promise<void>;
  logout: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

async function postAuth(action: string, body: Record<string, unknown> = {}) {
  const res = await fetch("/api/auth", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({ action, ...body }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data?.error || "Something went wrong. Please try again.");
  }
  return data;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [status, setStatus] = useState<AuthStatus>("loading");

  const refresh = useCallback(async () => {
    try {
      const res = await fetch("/api/auth", { credentials: "include" });
      if (res.status === 404) {
        // Auth backend isn't deployed (e.g. local `next dev`, where the Python
        // api/*.py don't run) - keep the app open for frontend-only work.
        setStatus("unavailable");
        setUser(null);
        return;
      }
      if (!res.ok) {
        // Deployed but not authenticated (401/403) or a transient 5xx: treat as
        // signed out so the gate sends the user to /login rather than exposing
        // the app shell during a backend hiccup.
        setStatus("anon");
        setUser(null);
        return;
      }
      const data = await res.json();
      if (data?.user) {
        setUser(data.user as AuthUser);
        setStatus("authed");
      } else {
        setUser(null);
        setStatus("anon");
      }
    } catch {
      setStatus("unavailable");
      setUser(null);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const login = useCallback(async (email: string, password: string) => {
    const data = await postAuth("login", { email, password });
    setUser(data.user as AuthUser);
    setStatus("authed");
  }, []);

  const signup = useCallback(async (email: string, password: string, orgName: string) => {
    const data = await postAuth("signup", { email, password, orgName });
    setUser(data.user as AuthUser);
    setStatus("authed");
  }, []);

  const logout = useCallback(async () => {
    await postAuth("logout").catch(() => {});
    setUser(null);
    setStatus("anon");
  }, []);

  return (
    <AuthContext.Provider value={{ user, status, refresh, login, signup, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within <AuthProvider>");
  return ctx;
}
