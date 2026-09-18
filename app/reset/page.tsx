"use client";

// Landing page for the emailed password-reset link: /reset?token=...
// Verifies the token by posting the chosen new password to api/auth
// (reset-password-with-token). Wrapped in Suspense because useSearchParams
// requires it under the App Router.

import { Suspense, useState, type FormEvent } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { PasswordInput } from "@/components/ui";

function ResetForm() {
  const router = useRouter();
  const params = useSearchParams();
  const token = params.get("token") ?? "";

  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    if (password !== confirm) {
      setError("Passwords don't match.");
      return;
    }
    setBusy(true);
    try {
      const res = await fetch("/api/auth", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ action: "reset-password-with-token", token, newPassword: password }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data?.error || "Could not reset the password.");
      setDone(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not reset the password.");
      setBusy(false);
    }
  }

  const inputCls =
    "w-full rounded-[var(--seo-radius-sm)] border border-[var(--seo-border-strong)] bg-[var(--seo-card-bg)] px-3.5 py-2.5 text-[14px] text-[var(--seo-text)] outline-none transition focus:border-[var(--seo-accent)] focus:ring-2 focus:ring-[var(--seo-accent-light)]";
  const labelCls = "mb-1.5 block text-[12.5px] font-semibold text-[var(--seo-subheading)]";

  return (
    <div className="flex min-h-screen items-center justify-center bg-[var(--seo-app-bg)] px-4 py-10">
      <div className="w-full max-w-[400px]">
        <div className="mb-7 flex items-center gap-2.5">
          <div
            className="flex h-9 w-9 items-center justify-center rounded-[var(--seo-radius-sm)] text-white"
            style={{ background: "var(--seo-gradient)" }}
            aria-hidden="true"
          >
            <svg width={18} height={18} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
              <circle cx={11} cy={11} r={7} />
              <path d="m21 21-4.3-4.3" />
            </svg>
          </div>
          <span className="text-[15px] font-bold tracking-tight text-[var(--seo-heading)]">SEO Technical Audit</span>
        </div>

        <div className="rounded-[var(--seo-radius-lg)] border border-[var(--seo-border)] bg-[var(--seo-card-bg)] p-6 shadow-[var(--seo-shadow-lg)] sm:p-7">
          <h1 className="text-[20px] font-bold tracking-tight text-[var(--seo-heading)]">Choose a new password</h1>

          {!token ? (
            <p className="mt-3 text-[13.5px] text-[var(--seo-error)]">
              This reset link is missing its token. Please use the link from your email, or request a new one.
            </p>
          ) : done ? (
            <div className="mt-4">
              <p className="text-[13.5px] text-[var(--seo-text-light)]">
                Your password has been changed. You can now sign in.
              </p>
              <button
                type="button"
                onClick={() => router.replace("/login")}
                className="mt-4 w-full rounded-[var(--seo-radius-sm)] btn-gradient px-4 py-2.5 text-[14px] font-semibold text-white"
              >
                Go to sign in
              </button>
            </div>
          ) : (
            <form onSubmit={onSubmit} className="mt-6 flex flex-col gap-4">
              <div>
                <label className={labelCls} htmlFor="password">New password</label>
                <PasswordInput
                  id="password"
                  className={inputCls}
                  value={password}
                  onChange={setPassword}
                  placeholder="At least 8 characters"
                  required
                  minLength={8}
                  autoComplete="new-password"
                />
              </div>
              <div>
                <label className={labelCls} htmlFor="confirm">Confirm new password</label>
                <PasswordInput
                  id="confirm"
                  className={inputCls}
                  value={confirm}
                  onChange={setConfirm}
                  placeholder="Re-enter the password"
                  required
                  minLength={8}
                  autoComplete="new-password"
                />
              </div>

              {error && (
                <div
                  role="alert"
                  className="rounded-[var(--seo-radius-sm)] border px-3.5 py-2.5 text-[13px]"
                  style={{ background: "var(--seo-error-bg)", borderColor: "var(--seo-error-border)", color: "var(--seo-error)" }}
                >
                  {error}
                </div>
              )}

              <button
                type="submit"
                disabled={busy}
                className="mt-1 rounded-[var(--seo-radius-sm)] btn-gradient px-4 py-2.5 text-[14px] font-semibold text-white transition disabled:opacity-60"
              >
                {busy ? "Please wait…" : "Set new password"}
              </button>
            </form>
          )}
        </div>

        <p className="mt-5 text-center text-[13px] text-[var(--seo-text-light)]">
          <button
            type="button"
            onClick={() => router.replace("/login")}
            className="font-semibold text-[var(--seo-accent)] hover:underline"
          >
            Back to sign in
          </button>
        </p>
      </div>
    </div>
  );
}

export default function ResetPage() {
  return (
    <Suspense fallback={null}>
      <ResetForm />
    </Suspense>
  );
}
