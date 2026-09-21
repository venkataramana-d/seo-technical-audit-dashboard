"use client";

import { useEffect, useState } from "react";
import { useAudit } from "@/lib/state/AuditContext";
import { useAuth } from "@/lib/state/AuthContext";
import { Card, PageHeader, PasswordInput } from "@/components/ui";
import { MoonIcon, SettingsIcon, SunIcon } from "@/components/icons";
import { useTheme } from "@/lib/useTheme";
import { useAiConfigStatus } from "@/lib/useAiConfigStatus";

interface VaultKey {
  provider: string;
  createdAt: string;
  maskedPreview: string;
}

const VAULT_PROVIDERS: { id: string; label: string; testable: boolean }[] = [
  { id: "psi", label: "PageSpeed Insights", testable: true },
  { id: "groq", label: "Groq (AI Summary)", testable: true },
  { id: "gsc", label: "Google Search Console", testable: false },
  { id: "ga4", label: "Google Analytics 4", testable: false },
  { id: "openai", label: "OpenAI", testable: false },
  { id: "anthropic", label: "Anthropic", testable: false },
  { id: "gemini", label: "Gemini", testable: false },
];

async function postApiKeysAction<T>(body: Record<string, unknown>): Promise<T> {
  const res = await fetch("/api/api-keys", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || "Request failed.");
  return data as T;
}

function ApiKeyVaultCard() {
  const [vaultKeys, setVaultKeys] = useState<VaultKey[] | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [busyProvider, setBusyProvider] = useState<string | null>(null);
  const [testResults, setTestResults] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);

  async function loadKeys() {
    try {
      const res = await fetch("/api/api-keys");
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Failed to load API keys.");
      setVaultKeys(data.apiKeys);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load API keys.");
    }
  }

  useEffect(() => {
    loadKeys();
  }, []);

  async function save(provider: string) {
    const value = (drafts[provider] || "").trim();
    if (!value) return;
    setBusyProvider(provider);
    setError(null);
    try {
      await postApiKeysAction({ action: "set", provider, value });
      setDrafts((d) => ({ ...d, [provider]: "" }));
      await loadKeys();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save key.");
    } finally {
      setBusyProvider(null);
    }
  }

  async function remove(provider: string) {
    setBusyProvider(provider);
    setError(null);
    try {
      await postApiKeysAction({ action: "delete", provider });
      await loadKeys();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete key.");
    } finally {
      setBusyProvider(null);
    }
  }

  async function test(provider: string) {
    setBusyProvider(provider);
    setTestResults((r) => ({ ...r, [provider]: "Testing…" }));
    try {
      const result = await postApiKeysAction<{ ok: boolean; error?: string }>({ action: "test", provider });
      setTestResults((r) => ({ ...r, [provider]: result.ok ? "Connection OK" : result.error || "Failed" }));
    } catch (err) {
      setTestResults((r) => ({ ...r, [provider]: err instanceof Error ? err.message : "Failed" }));
    } finally {
      setBusyProvider(null);
    }
  }

  const configuredByProvider = new Map((vaultKeys || []).map((k) => [k.provider, k]));

  return (
    <Card className="mb-4">
      <h3 className="mb-2 text-sm font-semibold text-[var(--seo-subheading)]">API Key Vault</h3>
      <p className="mb-3 text-sm text-[var(--seo-text-light)]">
        Saved server-side, encrypted at rest - separate from the per-browser Groq key below
        and the <code>PSI_API_KEY</code> environment variable. Used automatically wherever
        those providers are already integrated (PSI, Groq), falling back to the env var if unset.
      </p>
      {error ? <p className="mb-3 text-xs text-[var(--seo-error)]">{error}</p> : null}
      <div className="flex flex-col gap-3">
        {VAULT_PROVIDERS.map(({ id, label, testable }) => {
          const configured = configuredByProvider.get(id);
          const busy = busyProvider === id;
          return (
            <div key={id} className="rounded-lg border border-[var(--seo-border)] p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="text-sm font-medium text-[var(--seo-text)]">{label}</span>
                <span className="text-xs text-[var(--seo-muted)]">
                  {vaultKeys === null ? "Loading…" : configured ? configured.maskedPreview : "Not configured"}
                </span>
              </div>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <input
                  type="password"
                  value={drafts[id] || ""}
                  onChange={(e) => setDrafts((d) => ({ ...d, [id]: e.target.value }))}
                  placeholder={configured ? "Enter a new value to replace it" : "Paste API key"}
                  className="min-w-0 flex-1 rounded-lg border border-[var(--seo-border-strong)] bg-[var(--seo-card-bg)] px-2.5 py-1.5 text-sm text-[var(--seo-text)] outline-none focus:border-[var(--seo-accent)]"
                />
                <button
                  type="button"
                  onClick={() => save(id)}
                  disabled={busy || !(drafts[id] || "").trim()}
                  className="rounded-lg btn-gradient px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-60"
                >
                  Save
                </button>
                {configured ? (
                  <button
                    type="button"
                    onClick={() => remove(id)}
                    disabled={busy}
                    className="rounded-lg border border-[var(--seo-error-border)] px-3 py-1.5 text-xs font-medium text-[var(--seo-error)] hover:bg-[var(--seo-error-bg)] disabled:opacity-60"
                  >
                    Delete
                  </button>
                ) : null}
                {testable && configured ? (
                  <button
                    type="button"
                    onClick={() => test(id)}
                    disabled={busy}
                    className="rounded-lg border border-[var(--seo-border)] px-3 py-1.5 text-xs font-medium text-[var(--seo-text)] hover:bg-[var(--seo-card-hover)] disabled:opacity-60"
                  >
                    Test Connection
                  </button>
                ) : null}
              </div>
              {testResults[id] ? (
                <p className="mt-1.5 text-xs text-[var(--seo-muted)]">{testResults[id]}</p>
              ) : null}
            </div>
          );
        })}
      </div>
    </Card>
  );
}

function VaultAdminOnlyNotice() {
  return (
    <Card className="mb-4">
      <h3 className="mb-2 text-sm font-semibold text-[var(--seo-subheading)]">API Key Vault</h3>
      <p className="text-sm text-[var(--seo-text-light)]">
        Shared provider API keys (PageSpeed, Groq, etc.) are managed by your workspace
        admin. Ask an admin if a provider needs to be added or updated.
      </p>
    </Card>
  );
}

function ChangePasswordCard() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  async function submit() {
    setBusy(true);
    setMsg(null);
    try {
      const res = await fetch("/api/auth", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ action: "change-password", currentPassword: current, newPassword: next }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data?.error || "Could not change the password.");
      setMsg({ ok: true, text: "Password changed." });
      setCurrent("");
      setNext("");
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : "Could not change the password." });
    } finally {
      setBusy(false);
    }
  }

  const inputCls =
    "w-full rounded-[var(--seo-radius-sm)] border border-[var(--seo-border-strong)] bg-[var(--seo-card-bg)] px-3 py-2 text-[13.5px] text-[var(--seo-text)] outline-none focus:border-[var(--seo-accent)]";

  return (
    <Card className="mb-4">
      <h3 className="mb-2 text-sm font-semibold text-[var(--seo-subheading)]">Change password</h3>
      <p className="mb-3 text-sm text-[var(--seo-text-light)]">
        Update your account password. If an admin issued you a temporary password, change it here.
      </p>
      <div className="flex max-w-[360px] flex-col gap-2.5">
        <PasswordInput
          className={inputCls}
          placeholder="Current password"
          value={current}
          autoComplete="current-password"
          onChange={setCurrent}
        />
        <PasswordInput
          className={inputCls}
          placeholder="New password (at least 8 characters)"
          value={next}
          minLength={8}
          autoComplete="new-password"
          onChange={setNext}
        />
        <button
          type="button"
          disabled={busy || current.length < 1 || next.length < 8}
          onClick={submit}
          className="self-start rounded-[var(--seo-radius-sm)] btn-gradient px-4 py-2 text-[13.5px] font-semibold text-white transition disabled:opacity-50"
        >
          {busy ? "Saving…" : "Change password"}
        </button>
        {msg && (
          <p className={`text-[13px] ${msg.ok ? "text-[var(--seo-accent)]" : "text-[var(--seo-error)]"}`}>
            {msg.text}
          </p>
        )}
      </div>
    </Card>
  );
}

export default function SettingsPage() {
  const { results, clearAll, groqApiKey, setGroqApiKey, profile, setProfile } = useAudit();
  const { dark, setDark } = useTheme();
  const { psiConfigured, groqConfigured } = useAiConfigStatus();
  const { user, status } = useAuth();
  const [confirmClear, setConfirmClear] = useState(false);

  // Only show the editable vault manager to a positively-known admin. Anyone
  // whose role is not "admin" (including an authed user with role null) gets the
  // read-only notice. In local dev the auth backend is "unavailable" (no roles
  // enforced), so the manager stays visible there.
  const isAdmin = user?.role === "admin";
  const canManageVault = isAdmin || status === "unavailable";

  return (
    <div>
      <PageHeader icon={<SettingsIcon size={18} />} title="Settings" />

      <ChangePasswordCard />

      <Card className="mb-4">
        <h3 className="mb-2 text-sm font-semibold text-[var(--seo-subheading)]">
          Appearance
        </h3>
        <p className="mb-3 text-sm text-[var(--seo-text-light)]">
          Switch between light and dark mode. Your choice is saved in this browser and
          applies everywhere, in sync with the toggle in the top navigation bar.
        </p>
        <div className="flex items-center gap-2.5">
          <SunIcon
            size={16}
            className={dark ? "text-[var(--seo-muted)]" : "text-[var(--seo-accent)]"}
          />
          <button
            type="button"
            role="switch"
            aria-checked={dark}
            aria-label={dark ? "Switch to light mode" : "Switch to dark mode"}
            onClick={() => setDark(!dark)}
            className="relative h-6 w-11 shrink-0 rounded-full transition-colors"
            style={{ backgroundColor: dark ? "var(--seo-accent)" : "var(--seo-border-strong)" }}
          >
            <span
              className="absolute top-0.5 h-5 w-5 rounded-full bg-white shadow-sm transition-transform"
              style={{ transform: dark ? "translateX(22px)" : "translateX(2px)" }}
            />
          </button>
          <MoonIcon
            size={16}
            className={dark ? "text-[var(--seo-accent)]" : "text-[var(--seo-muted)]"}
          />
          <span className="ml-1 text-sm font-medium text-[var(--seo-text)]">
            {dark ? "Dark" : "Light"}
          </span>
        </div>
      </Card>

      <Card className="mb-4">
        <h3 className="mb-2 text-sm font-semibold text-[var(--seo-subheading)]">
          Profile
        </h3>
        <p className="mb-3 text-sm text-[var(--seo-text-light)]">
          Optional display info - this is saved only in this browser, the same way
          the Groq key below is, and is separate from your account sign-in.
        </p>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <div>
            <label className="mb-1 block text-xs font-medium text-[var(--seo-muted)]">Name</label>
            <input
              type="text"
              value={profile.name}
              onChange={(e) => setProfile({ ...profile, name: e.target.value })}
              placeholder="Your name"
              className="w-full rounded-lg border border-[var(--seo-border-strong)] bg-[var(--seo-card-bg)] px-3 py-2 text-sm text-[var(--seo-text)] outline-none focus:border-[var(--seo-accent)]"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-[var(--seo-muted)]">Email</label>
            <input
              type="email"
              value={profile.email}
              onChange={(e) => setProfile({ ...profile, email: e.target.value })}
              placeholder="you@company.com"
              className="w-full rounded-lg border border-[var(--seo-border-strong)] bg-[var(--seo-card-bg)] px-3 py-2 text-sm text-[var(--seo-text)] outline-none focus:border-[var(--seo-accent)]"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-[var(--seo-muted)]">Organization</label>
            <input
              type="text"
              value={profile.organization}
              onChange={(e) => setProfile({ ...profile, organization: e.target.value })}
              placeholder="Company name"
              className="w-full rounded-lg border border-[var(--seo-border-strong)] bg-[var(--seo-card-bg)] px-3 py-2 text-sm text-[var(--seo-text)] outline-none focus:border-[var(--seo-accent)]"
            />
          </div>
        </div>
      </Card>

      <Card className="mb-4">
        <h3 className="mb-2 text-sm font-semibold text-[var(--seo-subheading)]">
          PageSpeed Insights API Key
        </h3>
        <p className="mb-3 text-sm text-[var(--seo-text-light)]">
          Used for live Core Web Vitals data on the Performance Audit page. Without
          a key, PageSpeed still works via Google&apos;s anonymous quota (100
          requests/day per IP). A key raises that to 25,000/day.
        </p>
        <div className="flex items-center gap-2 text-sm">
          <span
            className="inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold"
            style={{
              color: psiConfigured ? "var(--seo-success)" : "var(--seo-warning)",
              backgroundColor: psiConfigured ? "var(--seo-success-bg)" : "var(--seo-warning-bg)",
            }}
          >
            {psiConfigured === null ? "Checking…" : psiConfigured ? "Configured" : "Not configured"}
          </span>
        </div>
        <p className="mt-3 text-xs text-[var(--seo-muted)]">
          To set it: open this project in the Vercel dashboard → Settings →
          Environment Variables → add <code>PSI_API_KEY</code> → redeploy.
        </p>
      </Card>

      <Card className="mb-4">
        <h3 className="mb-2 text-sm font-semibold text-[var(--seo-subheading)]">
          Groq AI Summary API Key
        </h3>
        <p className="mb-3 text-sm text-[var(--seo-text-light)]">
          Powers the plain-English AI summary on the URL Detail page (free tier at{" "}
          <a href="https://console.groq.com/keys" target="_blank" rel="noreferrer" className="underline">
            console.groq.com/keys
          </a>
          ). Without a key, audits still run fully. Only the AI summary is unavailable.
        </p>
        <div className="mb-3 flex items-center gap-2 text-sm">
          <span
            className="inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold"
            style={{
              color: groqConfigured || groqApiKey ? "var(--seo-success)" : "var(--seo-warning)",
              backgroundColor: groqConfigured || groqApiKey ? "var(--seo-success-bg)" : "var(--seo-warning-bg)",
            }}
          >
            {groqConfigured === null
              ? "Checking…"
              : groqApiKey
                ? "Using key from this browser"
                : groqConfigured
                  ? "Configured (server default)"
                  : "Not configured"}
          </span>
        </div>
        <input
          type="password"
          value={groqApiKey}
          onChange={(e) => setGroqApiKey(e.target.value)}
          placeholder="gsk_..."
          className="w-full rounded-lg border border-[var(--seo-border-strong)] bg-[var(--seo-card-bg)] px-3 py-2 text-sm text-[var(--seo-text)] outline-none focus:border-[var(--seo-accent)]"
        />
        <p className="mt-2 text-xs text-[var(--seo-muted)]">
          Stored only in this browser&apos;s IndexedDB and sent directly to the audit
          summary endpoint, never saved server-side.
        </p>
      </Card>

      {canManageVault ? <ApiKeyVaultCard /> : <VaultAdminOnlyNotice />}

      <Card>
        <h3 className="mb-2 text-sm font-semibold text-[var(--seo-subheading)]">
          Session Data
        </h3>
        <p className="mb-3 text-sm text-[var(--seo-text-light)]">
          {results.length} audit result(s) stored in this browser (IndexedDB).
        </p>
        <button
          type="button"
          onClick={() => {
            if (!confirmClear) {
              setConfirmClear(true);
              return;
            }
            clearAll();
            setConfirmClear(false);
          }}
          className="rounded-lg border border-[var(--seo-error-border)] px-3 py-1.5 text-sm font-medium text-[var(--seo-error)] hover:bg-[var(--seo-error-bg)]"
        >
          {confirmClear ? "Confirm clear all audit data?" : "Clear all audit data"}
        </button>
      </Card>
    </div>
  );
}
