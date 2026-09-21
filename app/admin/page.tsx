"use client";

// Admin-only portal: manage users and resolve password-reset requests.
// Role-gated both client-side (redirect non-admins) and server-side (every
// admin-* action calls require_admin in api/auth.py). Password resets issue a
// one-time temporary password the admin shares with the user out-of-band - no
// email service is involved (see worker/auth.py).

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/state/AuthContext";
import { Card, EmptyState, PageHeader } from "@/components/ui";
import { ShieldIcon } from "@/components/icons";

type AdminUser = { id: number; email: string; role: string | null; createdAt: string | null };
type ResetRequest = {
  id: number;
  email: string;
  userId: number | null;
  hasAccount: boolean;
  status: string;
  createdAt: string | null;
};

async function adminPost(action: string, body: Record<string, unknown> = {}) {
  const res = await fetch("/api/auth", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({ action, ...body }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data?.error || "Request failed.");
  return data;
}

function fmtDate(iso: string | null): string {
  if (!iso) return "-";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? "-" : d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

export default function AdminPage() {
  const router = useRouter();
  const { user, status } = useAuth();
  const isAdmin = user?.role === "admin";

  const [users, setUsers] = useState<AdminUser[]>([]);
  const [requests, setRequests] = useState<ResetRequest[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  // The one-time temp password to display after a reset, keyed to a user email.
  const [tempResult, setTempResult] = useState<{ email: string; tempPassword: string } | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  // Client-side gate: bounce non-admins (server also enforces it).
  useEffect(() => {
    if (status === "authed" && !isAdmin) router.replace("/");
  }, [status, isAdmin, router]);

  // Plain async function (mirrors ApiKeyVaultCard.loadKeys): every setState runs
  // after the first await, so nothing updates state synchronously in the effect.
  async function load() {
    try {
      const [u, r] = await Promise.all([
        adminPost("admin-list-users"),
        adminPost("admin-list-reset-requests", { status: "pending" }),
      ]);
      setUsers((u.users as AdminUser[]) ?? []);
      setRequests((r.requests as ResetRequest[]) ?? []);
      setError("");
      setLoading(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load admin data.");
      setLoading(false);
    }
  }

  useEffect(() => {
    let active = true;
    (async () => {
      // Not a known admin (non-admin, or status "unavailable" in local dev):
      // there's nothing to load, so drop the loading state instead of spinning
      // forever. Done inside the async IIFE so it isn't a synchronous setState
      // in the effect body (react-hooks/set-state-in-effect).
      if (!isAdmin) {
        if (active) setLoading(false);
        return;
      }
      try {
        const [u, r] = await Promise.all([
          adminPost("admin-list-users"),
          adminPost("admin-list-reset-requests", { status: "pending" }),
        ]);
        if (!active) return;
        setUsers((u.users as AdminUser[]) ?? []);
        setRequests((r.requests as ResetRequest[]) ?? []);
        setError("");
      } catch (err) {
        if (active) setError(err instanceof Error ? err.message : "Could not load admin data.");
      } finally {
        if (active) setLoading(false);
      }
    })();
    return () => {
      active = false;
    };
  }, [isAdmin]);

  async function resetUser(u: AdminUser) {
    if (!confirm(`Reset the password for ${u.email}? They'll need the new temporary password to sign in.`)) return;
    setBusyId(`user-${u.id}`);
    setError("");
    try {
      const data = await adminPost("admin-reset-user", { userId: u.id });
      setTempResult({ email: data.email, tempPassword: data.tempPassword });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not reset the password.");
    } finally {
      setBusyId(null);
    }
  }

  async function resolveRequest(req: ResetRequest) {
    setBusyId(`req-${req.id}`);
    setError("");
    try {
      const data = await adminPost("admin-resolve-reset", { requestId: req.id });
      setTempResult({ email: data.email, tempPassword: data.tempPassword });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not resolve the request.");
      await load();
    } finally {
      setBusyId(null);
    }
  }

  if (status === "loading" || (status === "authed" && !isAdmin)) {
    return (
      <div className="flex min-h-[40vh] items-center justify-center">
        <div className="h-6 w-6 animate-spin rounded-full border-2 border-[var(--seo-border-strong)] border-t-[var(--seo-accent)]" aria-label="Loading" />
      </div>
    );
  }

  const btn =
    "rounded-[var(--seo-radius-sm)] border border-[var(--seo-border-strong)] px-3 py-1.5 text-[12.5px] font-semibold text-[var(--seo-text)] transition hover:bg-[var(--seo-card-hover)] disabled:opacity-50";

  return (
    <div>
      <PageHeader
        title="Admin"
        subtitle="Manage users and password-reset requests for this workspace."
        icon={<ShieldIcon size={18} />}
      />

      {error && (
        <div
          role="alert"
          className="mb-4 rounded-[var(--seo-radius-sm)] border px-3.5 py-2.5 text-[13px]"
          style={{ background: "var(--seo-error-bg)", borderColor: "var(--seo-error-border)", color: "var(--seo-error)" }}
        >
          {error}
        </div>
      )}

      {/* One-time temp password banner */}
      {tempResult && (
        <Card className="mb-5 border-[var(--seo-accent)]">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="text-[13px] font-semibold text-[var(--seo-heading)]">
                Temporary password for {tempResult.email}
              </p>
              <p className="mt-0.5 text-[12.5px] text-[var(--seo-text-light)]">
                Share this with the user securely. It won&apos;t be shown again - they can change it after signing in.
              </p>
              <code className="mt-2 inline-block rounded bg-[var(--seo-card-hover)] px-2.5 py-1 font-mono text-[14px] text-[var(--seo-heading)]">
                {tempResult.tempPassword}
              </code>
            </div>
            <div className="flex gap-2">
              <button
                type="button"
                className={btn}
                onClick={() => navigator.clipboard?.writeText(tempResult.tempPassword)}
              >
                Copy
              </button>
              <button type="button" className={btn} onClick={() => setTempResult(null)}>
                Dismiss
              </button>
            </div>
          </div>
        </Card>
      )}

      {/* Pending reset requests */}
      <h2 className="mb-2.5 text-[15px] font-semibold text-[var(--seo-heading)]">
        Pending password-reset requests
        {requests.length > 0 ? (
          <span className="ml-2 rounded-full bg-[var(--seo-error)] px-2 py-0.5 text-[11px] font-bold text-white">
            {requests.length}
          </span>
        ) : null}
      </h2>
      <Card className="mb-8 p-0">
        {loading ? (
          <p className="p-5 text-[13px] text-[var(--seo-text-light)]">Loading…</p>
        ) : requests.length === 0 ? (
          <div className="p-5">
            <EmptyState title="No pending requests" hint="When a user submits “Forgot password?”, it appears here." />
          </div>
        ) : (
          <table className="w-full text-[13px]">
            <thead>
              <tr className="border-b border-[var(--seo-border)] text-left text-[var(--seo-muted)]">
                <th className="px-4 py-2.5 font-semibold">Email</th>
                <th className="px-4 py-2.5 font-semibold">Account</th>
                <th className="px-4 py-2.5 font-semibold">Requested</th>
                <th className="px-4 py-2.5 text-right font-semibold">Action</th>
              </tr>
            </thead>
            <tbody>
              {requests.map((req) => (
                <tr key={req.id} className="border-b border-[var(--seo-border)] last:border-0">
                  <td className="px-4 py-2.5 text-[var(--seo-text)]">{req.email}</td>
                  <td className="px-4 py-2.5">
                    {req.hasAccount ? (
                      <span className="text-[var(--seo-text-light)]">Exists</span>
                    ) : (
                      <span className="text-[var(--seo-error)]">No account</span>
                    )}
                  </td>
                  <td className="px-4 py-2.5 text-[var(--seo-text-light)]">{fmtDate(req.createdAt)}</td>
                  <td className="px-4 py-2.5 text-right">
                    <button
                      type="button"
                      className={btn}
                      disabled={busyId === `req-${req.id}`}
                      onClick={() => resolveRequest(req)}
                    >
                      {busyId === `req-${req.id}` ? "…" : req.hasAccount ? "Issue new password" : "Clear"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      {/* Users */}
      <h2 className="mb-2.5 text-[15px] font-semibold text-[var(--seo-heading)]">Users</h2>
      <Card className="p-0">
        {loading ? (
          <p className="p-5 text-[13px] text-[var(--seo-text-light)]">Loading…</p>
        ) : users.length === 0 ? (
          <div className="p-5">
            <EmptyState title="No users yet" />
          </div>
        ) : (
          <table className="w-full text-[13px]">
            <thead>
              <tr className="border-b border-[var(--seo-border)] text-left text-[var(--seo-muted)]">
                <th className="px-4 py-2.5 font-semibold">Email</th>
                <th className="px-4 py-2.5 font-semibold">Role</th>
                <th className="px-4 py-2.5 font-semibold">Joined</th>
                <th className="px-4 py-2.5 text-right font-semibold">Action</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id} className="border-b border-[var(--seo-border)] last:border-0">
                  <td className="px-4 py-2.5 text-[var(--seo-text)]">{u.email}</td>
                  <td className="px-4 py-2.5">
                    <span
                      className={`rounded-full px-2 py-0.5 text-[11px] font-bold ${
                        u.role === "admin"
                          ? "bg-[var(--seo-accent-light)] text-[var(--seo-accent)]"
                          : "bg-[var(--seo-card-hover)] text-[var(--seo-text-light)]"
                      }`}
                    >
                      {u.role ?? "-"}
                    </span>
                  </td>
                  <td className="px-4 py-2.5 text-[var(--seo-text-light)]">{fmtDate(u.createdAt)}</td>
                  <td className="px-4 py-2.5 text-right">
                    <button
                      type="button"
                      className={btn}
                      disabled={busyId === `user-${u.id}`}
                      onClick={() => resetUser(u)}
                    >
                      {busyId === `user-${u.id}` ? "…" : "Reset password"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
