"use client";

import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import type { AuditResult, NavFilter } from "@/lib/types";
import { idbGet, idbSet } from "@/lib/state/idbStore";
import type { AiSummaryCacheEntry } from "@/lib/aiSummaryCache";

const STORAGE_KEY = "seo-audit-dashboard-state-v1";
// A bulk audit's results routinely exceed localStorage's ~5-10MB per-origin
// quota (each full audit_url() result can be 50-200KB; 200 URLs adds up
// fast). IndexedDB's quota is a share of free disk space, comfortably larger.
// Legacy localStorage data is migrated in once, then removed.
const LEGACY_LOCALSTORAGE_KEY = STORAGE_KEY;
// Hard ceiling if a save still fails somehow (e.g. IndexedDB unavailable,
// disk genuinely full): keep only the most recent N results rather than
// losing everything or crashing the app.
const MAX_STORED_RESULTS = 500;

export interface Profile {
  name: string;
  email: string;
  organization: string;
}

const EMPTY_PROFILE: Profile = { name: "", email: "", organization: "" };

interface PersistedState {
  results: AuditResult[];
  lastAuditDate: string | null;
  groqApiKey: string;
  // Keyed by URL (or "__sitewide__" for the Results page rollup summary) so
  // reopening an unchanged result's AI Summary doesn't re-spend an API call;
  // see lib/aiSummaryCache.ts::fingerprintForSummary for invalidation.
  aiSummaryCache: Record<string, AiSummaryCacheEntry>;
  // This is optional display info the user can fill in, stored per-browser in
  // IndexedDB like everything else here. It's separate from the app's account
  // sign-in (see lib/state/AuthContext.tsx) -- not the authenticated identity.
  profile: Profile;
}

interface AuditContextValue {
  results: AuditResult[];
  lastAuditDate: string | null;
  selectedUrlIndex: number;
  navFilter: NavFilter | null;
  groqApiKey: string;
  storageWarning: string | null;
  aiSummaryCache: Record<string, AiSummaryCacheEntry>;
  profile: Profile;
  addResult: (result: AuditResult) => void;
  addResults: (results: AuditResult[]) => void;
  setSelectedUrlIndex: (index: number) => void;
  setNavFilter: (filter: NavFilter | null) => void;
  setGroqApiKey: (key: string) => void;
  setCachedAiSummary: (key: string, entry: AiSummaryCacheEntry) => void;
  setProfile: (profile: Profile) => void;
  clearAll: () => void;
}

const AuditContext = createContext<AuditContextValue | null>(null);

function normalizePersisted(parsed: unknown): PersistedState {
  const p = (parsed ?? {}) as Partial<PersistedState>;
  return {
    results: Array.isArray(p.results) ? p.results : [],
    lastAuditDate: p.lastAuditDate ?? null,
    groqApiKey: typeof p.groqApiKey === "string" ? p.groqApiKey : "",
    aiSummaryCache: p.aiSummaryCache && typeof p.aiSummaryCache === "object" ? p.aiSummaryCache : {},
    profile: {
      name: typeof p.profile?.name === "string" ? p.profile.name : "",
      email: typeof p.profile?.email === "string" ? p.profile.email : "",
      organization: typeof p.profile?.organization === "string" ? p.profile.organization : "",
    },
  };
}

async function loadPersisted(): Promise<PersistedState> {
  if (typeof window === "undefined") return normalizePersisted(null);
  try {
    const fromIdb = await idbGet<PersistedState>(STORAGE_KEY);
    if (fromIdb) return normalizePersisted(fromIdb);
  } catch {
    /* IndexedDB unavailable (e.g. some private-browsing modes); fall through */
  }
  // One-time migration from the old localStorage-backed version.
  try {
    const raw = window.localStorage.getItem(LEGACY_LOCALSTORAGE_KEY);
    if (raw) {
      const migrated = normalizePersisted(JSON.parse(raw));
      window.localStorage.removeItem(LEGACY_LOCALSTORAGE_KEY);
      idbSet(STORAGE_KEY, migrated).catch(() => {});
      return migrated;
    }
  } catch {
    /* ignore malformed legacy data */
  }
  return normalizePersisted(null);
}

export function AuditProvider({ children }: { children: ReactNode }) {
  const [results, setResults] = useState<AuditResult[]>([]);
  const [lastAuditDate, setLastAuditDate] = useState<string | null>(null);
  const [selectedUrlIndex, setSelectedUrlIndex] = useState(0);
  const [navFilter, setNavFilter] = useState<NavFilter | null>(null);
  const [groqApiKey, setGroqApiKey] = useState("");
  const [aiSummaryCache, setAiSummaryCache] = useState<Record<string, AiSummaryCacheEntry>>({});
  const [profile, setProfile] = useState<Profile>(EMPTY_PROFILE);
  const [hydrated, setHydrated] = useState(false);
  const [storageWarning, setStorageWarning] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    loadPersisted().then((persisted) => {
      if (cancelled) return;
      setResults(persisted.results);
      setLastAuditDate(persisted.lastAuditDate);
      setGroqApiKey(persisted.groqApiKey);
      setAiSummaryCache(persisted.aiSummaryCache);
      setProfile(persisted.profile);
      setHydrated(true);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!hydrated || typeof window === "undefined") return;
    const payload: PersistedState = { results, lastAuditDate, groqApiKey, aiSummaryCache, profile };
    idbSet(STORAGE_KEY, payload)
      .then(() => setStorageWarning(null))
      .catch(async () => {
        // Extremely unlikely with IndexedDB's much larger quota, but stay
        // resilient: prune to the most recent results and retry once rather
        // than crashing or silently losing everything.
        if (results.length <= MAX_STORED_RESULTS) {
          // Already at/under the cap: pruning would be a no-op and retrying
          // the same payload would fail identically, so just warn instead.
          setStorageWarning("Could not save audit results to browser storage. Recent results may not persist.");
          return;
        }
        const pruned = results.slice(0, MAX_STORED_RESULTS);
        try {
          await idbSet(STORAGE_KEY, { ...payload, results: pruned });
          setResults(pruned);
          setStorageWarning(
            `Storage limit reached. Kept the most recent ${MAX_STORED_RESULTS} results and dropped older ones.`,
          );
        } catch {
          setStorageWarning("Could not save audit results to browser storage. Recent results may not persist.");
        }
      });
  }, [results, lastAuditDate, groqApiKey, aiSummaryCache, profile, hydrated]);

  const value = useMemo<AuditContextValue>(
    () => ({
      results,
      lastAuditDate,
      selectedUrlIndex,
      navFilter,
      groqApiKey,
      storageWarning,
      aiSummaryCache,
      profile,
      addResult: (result) => {
        setResults((prev) => {
          const existingIdx = prev.findIndex((r) => r.url === result.url);
          if (existingIdx >= 0) {
            const next = [...prev];
            next[existingIdx] = result;
            return next;
          }
          return [result, ...prev];
        });
        setLastAuditDate(new Date().toISOString());
        setSelectedUrlIndex(0);
      },
      // Batched add for sitewide/bulk audits: one state update + one
      // localStorage write for N results (vs. N writes via addResult).
      // Upserts by URL: existing URLs are replaced, new ones prepended.
      addResults: (incoming) => {
        if (!incoming.length) return;
        setResults((prev) => {
          const byUrl = new Map(prev.map((r) => [r.url, r]));
          for (const r of incoming) byUrl.set(r.url, r);
          // New results first (incoming order), then prior results not re-audited.
          const incomingUrls = new Set(incoming.map((r) => r.url));
          const merged = [
            ...incoming.map((r) => byUrl.get(r.url)!),
            ...prev.filter((r) => !incomingUrls.has(r.url)),
          ];
          return merged;
        });
        setLastAuditDate(new Date().toISOString());
        // Unlike addResult (a single, user-initiated audit), addResults is
        // called repeatedly in the background while a bulk crawl is still
        // running (lib/crawl/chunkedRunner.ts flushes every few completed
        // URLs). Don't touch selectedUrlIndex here - doing so would yank the
        // Detail page to a different URL out from under a user reading an
        // already-audited result while the crawl keeps going behind the scenes.
      },
      setSelectedUrlIndex,
      setNavFilter,
      setGroqApiKey,
      setCachedAiSummary: (key, entry) => {
        setAiSummaryCache((prev) => ({ ...prev, [key]: entry }));
      },
      setProfile,
      clearAll: () => {
        setResults([]);
        setLastAuditDate(null);
        setSelectedUrlIndex(0);
        setNavFilter(null);
        setAiSummaryCache({});
      },
    }),
    [results, lastAuditDate, selectedUrlIndex, navFilter, groqApiKey, storageWarning, aiSummaryCache, profile],
  );

  return <AuditContext.Provider value={value}>{children}</AuditContext.Provider>;
}

export function useAudit() {
  const ctx = useContext(AuditContext);
  if (!ctx) throw new Error("useAudit must be used within AuditProvider");
  return ctx;
}
