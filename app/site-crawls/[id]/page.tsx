"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Card, PageHeader, ScoreCircle } from "@/components/ui";
import { GlobeIcon } from "@/components/icons";
import { formatDate } from "@/lib/format";
import { SCHEDULE_PRESETS, humanizeCron, presetIdForCron } from "@/lib/schedulePresets";

// Same convention as app/page.tsx's charts - Recharts tooltips ignore
// Tailwind classes, but CSS variables in inline styles still resolve
// against the current theme.
const CHART_TOOLTIP_STYLE = {
  backgroundColor: "var(--seo-card-bg)",
  border: "1px solid var(--seo-border-strong)",
  borderRadius: "8px",
  color: "var(--seo-text)",
  fontSize: "13px",
};
const CHART_TOOLTIP_LABEL_STYLE = { color: "var(--seo-heading)", fontWeight: 600 };

interface CrawlStatus {
  id: number;
  rootUrl: string | null;
  status: string;
  healthScore: number | null;
  seoScoreAvg: number | null;
  pagesCrawled: number;
  pagesTotalEstimate: number | null;
  startedAt: string | null;
  finishedAt: string | null;
  scheduleCron: string | null;
  nextRunAt: string | null;
}

interface ThemeIssue {
  issue: string;
  severity: string;
  recommendation: string;
}

interface ThemeReport {
  count: number;
  by_severity: Record<string, number>;
  issues: ThemeIssue[];
}

interface PageRow {
  id: number;
  url: string;
  statusCode: number | null;
  title: string | null;
  metaDescription: string | null;
  canonicalUrl: string | null;
  h1: string | null;
  seoScore: number | null;
  fetchedAt: string | null;
  issueCounts: Record<string, number>;
}

interface PagesResponse {
  pages: PageRow[];
  total: number;
  page: number;
  pageSize: number;
}

interface IssueRow {
  id: number;
  issueType: string;
  severity: string;
  category: string;
  recommendation: string;
  impactScore: number | null;
  effortLevel: string | null;
  pageUrl: string | null;
  createdAt: string | null;
}

interface IssuesResponse {
  issues: IssueRow[];
  total: number;
  page: number;
  pageSize: number;
  categories: string[];
}

interface LinkRow {
  id: number;
  targetUrl: string;
  linkType: string;
  domLocation: string | null;
  anchorText: string | null;
  isNofollow: boolean;
  isDofollow: boolean;
  statusCode: number | null;
  isBroken: boolean | null;
  pageUrl: string | null;
}

interface LinksResponse {
  links: LinkRow[];
  total: number;
  page: number;
  pageSize: number;
  linkTypeCounts: Record<string, number>;
}

interface CrawlListItem {
  id: number;
  rootUrl: string | null;
  status: string;
  healthScore: number | null;
  seoScoreAvg: number | null;
  pagesCrawled: number;
  startedAt: string | null;
  finishedAt: string | null;
}

interface DiffIssue {
  url: string | null;
  issue_type: string;
}

interface DiffPage {
  url: string;
  old_score: number;
  new_score: number;
  delta: number;
}

interface CompareResponse {
  available: boolean;
  compareToId?: number;
  diff?: {
    newIssues: DiffIssue[];
    fixedIssues: DiffIssue[];
    regressedPages: DiffPage[];
    improvedPages: DiffPage[];
    healthScoreDelta: number | null;
    seoScoreAvgDelta: number | null;
  };
}

interface TrendPoint {
  crawl_id: number;
  health_score: number | null;
  seo_score_avg: number | null;
  finished_at: string | null;
}

// Pause/Resume only make sense when a persistent worker drives the crawl
// (M2 T2.1). In the default browser-orchestrated mode the tab does the crawling
// and ignores DB status, so the controls would be misleading - hide them.
const SERVER_CRAWL = process.env.NEXT_PUBLIC_SERVER_CRAWL === "1";
const POLL_INTERVAL_MS = 3000;
const FINISHED_STATUSES = new Set(["completed", "failed"]);
const TABS = ["Overview", "Pages", "Issues", "Site-wide", "Links", "Compare"] as const;
type Tab = (typeof TABS)[number];

// Site-wide analysis (api/analyze.py) - the "brains" folded in from the rebuild.
interface SiteIssue {
  issueType: string;
  category: string;
  severity: string;
  impactScore: number | null;
  effortLevel: string | null;
  what: string;
  why: string;
  fix: string;
  affectedUrls: string[];
  affectedCount: number;
}
interface SitewideResponse {
  pageCount: number;
  linkCount: number;
  issueCount: number;
  issues: SiteIssue[];
}
interface CrawlGraphResponse {
  root: string;
  maxDepth: number;
  avgDepth: number;
  reachableCount: number;
  pagesPerDepth: Record<string, number>;
  unreachableUrls: string[];
  deepestPages: { url: string; depth: number }[];
  issues: SiteIssue[];
}

const SEVERITY_STYLE: Record<string, { color: string; bg: string }> = {
  error: { color: "var(--seo-error)", bg: "var(--seo-error-bg)" },
  warning: { color: "var(--seo-warning)", bg: "var(--seo-warning-bg)" },
  notice: { color: "var(--seo-accent)", bg: "var(--seo-accent-light)" },
};

function SeverityChip({ severity, count }: { severity: string; count: number }) {
  const s = SEVERITY_STYLE[severity] ?? SEVERITY_STYLE.notice;
  return (
    <span className="pill" style={{ color: s.color, backgroundColor: s.bg }}>
      {count} {severity}
    </span>
  );
}

function SeverityBadge({ severity }: { severity: string }) {
  const s = SEVERITY_STYLE[severity] ?? SEVERITY_STYLE.notice;
  return (
    <span className="pill capitalize" style={{ color: s.color, backgroundColor: s.bg }}>
      {severity}
    </span>
  );
}

/** Small colored square + text instead of a rounded pill badge - used inside
 * the dense Pages/Issues/Links grids specifically (a Screaming-Frog-style
 * data grid reads status via muted colored text, not big colorful chips).
 * SeverityChip/SeverityBadge (pill-based) stay as-is for the Overview tab's
 * thematic report and the filter-toggle buttons, which aren't grid content. */
function StatusDot({ color }: { color: string }) {
  return <span className="inline-block h-1.5 w-1.5 shrink-0 rounded-full" style={{ backgroundColor: color }} />;
}

const GRID_TH =
  "border border-[var(--table-row-border)] bg-[var(--table-header-bg)] px-2 py-1.5 text-left text-[11px] font-semibold uppercase tracking-wide text-[var(--seo-muted)]";
const GRID_TD = "border border-[var(--table-row-border)] px-2 py-1 align-top text-xs text-[var(--seo-text)]";

/** One flat, underlined, count-labeled tab strip - the Screaming-Frog-style
 * counterpart to `TabBar` (components/ui.tsx), scoped to this page only:
 * TabBar's rounded-pill look is shared across the single-URL detail page too
 * (components/detail/*View.tsx), so it isn't touched. */
function SpreadsheetTabBar({
  tabs,
  active,
  onChange,
}: {
  tabs: { key: Tab; label: string; count?: number | null }[];
  active: Tab;
  onChange: (tab: Tab) => void;
}) {
  return (
    <div className="mb-3 flex gap-5 border-b border-[var(--seo-border)]">
      {tabs.map((t) => {
        const isActive = t.key === active;
        return (
          <button
            key={t.key}
            type="button"
            onClick={() => onChange(t.key)}
            className="relative pb-2 text-xs font-semibold uppercase tracking-wide"
            style={{ color: isActive ? "var(--seo-accent)" : "var(--seo-muted)" }}
          >
            {t.label}
            {t.count != null ? <span className="ml-1.5 tabular-nums">({t.count.toLocaleString()})</span> : null}
            {isActive ? <span className="absolute inset-x-0 -bottom-px h-0.5 bg-[var(--seo-accent)]" /> : null}
          </button>
        );
      })}
    </div>
  );
}

type SelectedRow =
  | { type: "page"; data: PageRow }
  | { type: "issue"; data: IssueRow }
  | { type: "link"; data: LinkRow };

/** Fetches this specific page's own issues (via the "issues" action's pageId
 * filter) so the detail panel can show the real, specific findings for a
 * selected page - not just the severity counts already on hand from the
 * pages grid. Re-fetches whenever a different page is selected. */
function usePageIssues(crawlId: number, pageId: number | null) {
  const [issues, setIssues] = useState<IssueRow[] | null>(null);

  useEffect(() => {
    if (pageId == null) {
      setIssues(null);
      return;
    }
    let cancelled = false;
    setIssues(null);
    postCrawlsAction<IssuesResponse>({ action: "issues", crawlId, pageId, pageSize: 50 })
      .then((data) => {
        if (!cancelled) setIssues(data.issues);
      })
      .catch(() => {
        if (!cancelled) setIssues([]);
      });
    return () => {
      cancelled = true;
    };
  }, [crawlId, pageId]);

  return issues;
}

// Issue types the AI Content Agent can draft a fix for (mirrors
// modules/content_agent.py SUPPORTED_ISSUE_TYPES - title/meta only).
const AI_DRAFTABLE = /title|description|meta/i;

function AiDraftButton({ issue }: { issue: IssueRow }) {
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState<{ draftText: string; confidence: number | null; suggestionType: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    setBusy(true);
    setError(null);
    setDraft(null);
    try {
      const res = await fetch("/api/ai", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "content-draft", issueType: issue.issueType, url: issue.pageUrl }),
      });
      const data = await res.json();
      if (!res.ok || data.ok === false) throw new Error(data.error || "Could not generate a draft.");
      setDraft({ draftText: data.draftText, confidence: data.confidence ?? null, suggestionType: data.suggestionType });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not generate a draft.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mt-2 border-t border-[var(--seo-border)] pt-2.5">
      <button
        type="button"
        onClick={run}
        disabled={busy}
        className="inline-flex items-center gap-1.5 rounded-lg btn-gradient px-2.5 py-1.5 text-[12px] font-semibold text-white disabled:opacity-60"
      >
        <svg width={13} height={13} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
          <path d="M12 2l1.9 5.1L19 9l-5.1 1.9L12 16l-1.9-5.1L5 9l5.1-1.9L12 2z" />
        </svg>
        {busy ? "Drafting…" : draft ? "Redraft with AI" : "Draft a fix with AI"}
      </button>
      {error ? <p className="mt-1.5 text-[var(--seo-error)]">{error}</p> : null}
      {draft ? (
        <div className="mt-2 rounded-[var(--seo-radius-sm)] border border-[var(--seo-accent-border)] bg-[var(--seo-accent-light)] p-2.5">
          <div className="mb-1 flex items-center justify-between gap-2">
            <span className="text-[11px] font-semibold uppercase tracking-wide text-[var(--seo-accent)]">
              Suggested {draft.suggestionType.replace(/_/g, " ")}
            </span>
            {draft.confidence != null ? (
              <span className="text-[11px] tabular-nums text-[var(--seo-muted)]">
                confidence {Math.round(draft.confidence * 100)}%
              </span>
            ) : null}
          </div>
          <p className="text-[13px] text-[var(--seo-heading)]">{draft.draftText}</p>
          <p className="mt-1 text-[11px] text-[var(--seo-muted)]">Draft only - review before applying it to your page.</p>
        </div>
      ) : null}
    </div>
  );
}

function DetailPanel({
  crawlId,
  selected,
  onViewPage,
}: {
  crawlId: number;
  selected: SelectedRow | null;
  onViewPage: (url: string) => void;
}) {
  const pageIssues = usePageIssues(crawlId, selected?.type === "page" ? selected.data.id : null);

  if (!selected) {
    return (
      <Card>
        <p className="text-xs text-[var(--seo-muted)]">Select a row above to see its full details here.</p>
      </Card>
    );
  }

  if (selected.type === "page") {
    const p = selected.data;
    return (
      <Card>
        <h3 className="mb-2.5 text-[11px] font-semibold uppercase tracking-wide text-[var(--seo-muted)]">Page detail</h3>
        <div className="flex flex-col gap-1.5 text-xs">
          <div><span className="text-[var(--seo-muted)]">URL </span><span className="break-all font-mono text-[var(--seo-heading)]">{p.url}</span></div>
          <div><span className="text-[var(--seo-muted)]">Title </span>{p.title || "-"}</div>
          <div><span className="text-[var(--seo-muted)]">Meta description </span>{p.metaDescription || "-"}</div>
          <div><span className="text-[var(--seo-muted)]">Canonical </span><span className="break-all font-mono">{p.canonicalUrl || "-"}</span></div>
          <div><span className="text-[var(--seo-muted)]">H1 </span>{p.h1 || "-"}</div>
          <div className="mt-1">
            <span className="text-[var(--seo-muted)]">Issues on this page</span>
            {pageIssues === null ? (
              <p className="mt-1 text-[var(--seo-muted)]">Loading…</p>
            ) : pageIssues.length === 0 ? (
              <p className="mt-1 text-[var(--seo-success)]">None</p>
            ) : (
              <ul className="mt-1 flex flex-col gap-1">
                {pageIssues.map((iss) => (
                  <li key={iss.id} className="flex items-start gap-1.5">
                    <StatusDot color={(SEVERITY_STYLE[iss.severity] ?? SEVERITY_STYLE.notice).color} />
                    <span>
                      <span className="font-medium text-[var(--seo-heading)]">{iss.issueType}</span>
                      <span className="text-[var(--seo-text-light)]"> - {iss.recommendation}</span>
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </Card>
    );
  }

  if (selected.type === "issue") {
    const i = selected.data;
    return (
      <Card>
        <h3 className="mb-2.5 text-[11px] font-semibold uppercase tracking-wide text-[var(--seo-muted)]">Issue detail</h3>
        <div className="flex flex-col gap-1.5 text-xs">
          <div className="flex flex-wrap items-center gap-2">
            <SeverityBadge severity={i.severity} />
            <span className="font-medium text-[var(--seo-heading)]">{i.issueType}</span>
          </div>
          <div><span className="text-[var(--seo-muted)]">Category </span>{i.category}</div>
          <div><span className="text-[var(--seo-muted)]">Recommendation </span>{i.recommendation}</div>
          <div>
            <span className="text-[var(--seo-muted)]">Impact </span>{i.impactScore ?? "-"}
            <span className="ml-4 text-[var(--seo-muted)]">Effort </span>{i.effortLevel || "-"}
          </div>
          {i.pageUrl ? (
            <button
              type="button"
              onClick={() => onViewPage(i.pageUrl!)}
              className="mt-1 self-start break-all font-mono text-[var(--seo-accent)] hover:underline"
            >
              View page: {i.pageUrl}
            </button>
          ) : null}
          {AI_DRAFTABLE.test(i.issueType) ? <AiDraftButton issue={i} /> : null}
        </div>
      </Card>
    );
  }

  const l = selected.data;
  return (
    <Card>
      <h3 className="mb-2.5 text-[11px] font-semibold uppercase tracking-wide text-[var(--seo-muted)]">Link detail</h3>
      <div className="flex flex-col gap-1.5 text-xs">
        <div><span className="text-[var(--seo-muted)]">Target </span><span className="break-all font-mono">{l.targetUrl}</span></div>
        <div><span className="text-[var(--seo-muted)]">Anchor text </span>{l.anchorText || "-"}</div>
        <div>
          <span className="text-[var(--seo-muted)]">Type </span>{LINK_TYPE_LABELS[l.linkType] || l.linkType}
          <span className="ml-4 text-[var(--seo-muted)]">Location </span>{l.domLocation || "-"}
        </div>
        <div>
          <span className="text-[var(--seo-muted)]">Follow </span>{l.isNofollow ? "Nofollow" : "Dofollow"}
          <span className="ml-4 text-[var(--seo-muted)]">Status </span>
          {l.isBroken ? `Broken (${l.statusCode ?? "-"})` : (l.statusCode ?? "Not checked")}
        </div>
        {l.pageUrl ? (
          <button
            type="button"
            onClick={() => onViewPage(l.pageUrl!)}
            className="mt-1 self-start break-all font-mono text-[var(--seo-accent)] hover:underline"
          >
            From page: {l.pageUrl}
          </button>
        ) : null}
      </div>
    </Card>
  );
}

function PaginationControls({
  page,
  totalPages,
  onChange,
}: {
  page: number;
  totalPages: number;
  onChange: (page: number) => void;
}) {
  if (totalPages <= 1) return null;
  return (
    <div className="mt-3 flex items-center justify-end gap-2 text-xs">
      <button
        type="button"
        disabled={page <= 1}
        onClick={() => onChange(page - 1)}
        className="rounded-lg border border-[var(--seo-border)] px-2.5 py-1 text-[var(--seo-text)] hover:bg-[var(--seo-card-hover)] disabled:opacity-40"
      >
        Prev
      </button>
      <span className="text-[var(--seo-muted)]">
        Page {page} of {totalPages}
      </span>
      <button
        type="button"
        disabled={page >= totalPages}
        onClick={() => onChange(page + 1)}
        className="rounded-lg border border-[var(--seo-border)] px-2.5 py-1 text-[var(--seo-text)] hover:bg-[var(--seo-card-hover)] disabled:opacity-40"
      >
        Next
      </button>
    </div>
  );
}

async function postCrawlsAction<T>(body: Record<string, unknown>): Promise<T> {
  const res = await fetch("/api/crawls", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || "Request failed.");
  return data as T;
}

async function postAnalyzeAction<T>(action: string, crawlId: number): Promise<T> {
  const res = await fetch("/api/analyze", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, crawlId }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || "Analysis failed.");
  return data as T;
}

/** Shared fetch-on-filter-change plumbing for the Pages/Issues tabs - same
 * action-dispatch call, just a different action name and filter shape. */
function useCrawlListing<T>(action: "pages" | "issues" | "links", crawlId: number, filters: Record<string, unknown>) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const filtersKey = JSON.stringify(filters);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    postCrawlsAction<T>({ action, crawlId, ...filters })
      .then((result) => {
        if (!cancelled) {
          setData(result);
          setError(null);
        }
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [action, crawlId, filtersKey]);

  return { data, error, loading };
}

function ScheduleCard({
  crawlId,
  scheduleCron,
  nextRunAt,
  onChange,
}: {
  crawlId: number;
  scheduleCron: string | null;
  nextRunAt: string | null;
  onChange: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [preset, setPreset] = useState(() => presetIdForCron(scheduleCron));
  const [customCron, setCustomCron] = useState(scheduleCron && presetIdForCron(scheduleCron) === "custom" ? scheduleCron : "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save(nextCron: string | null) {
    setSaving(true);
    setError(null);
    try {
      await postCrawlsAction({ action: "setSchedule", crawlId, scheduleCron: nextCron });
      setEditing(false);
      onChange();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update schedule.");
    } finally {
      setSaving(false);
    }
  }

  function applyPreset() {
    const cron =
      preset === "off" ? null : preset === "custom" ? customCron.trim() || null : (SCHEDULE_PRESETS.find((p) => p.id === preset)?.cron ?? null);
    save(cron);
  }

  return (
    <Card>
      <h2 className="mb-3 text-sm font-semibold text-[var(--seo-heading)]">Schedule</h2>
      {!editing ? (
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <div className="text-sm text-[var(--seo-text)]">{humanizeCron(scheduleCron)}</div>
            {scheduleCron ? (
              <div className="mt-0.5 text-xs text-[var(--seo-muted)]">Next run {formatDate(nextRunAt)}</div>
            ) : null}
          </div>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => {
                setPreset(presetIdForCron(scheduleCron));
                setCustomCron(scheduleCron && presetIdForCron(scheduleCron) === "custom" ? scheduleCron : "");
                setEditing(true);
              }}
              className="rounded-lg border border-[var(--seo-border)] px-3 py-1.5 text-xs font-medium text-[var(--seo-text)] hover:bg-[var(--seo-card-hover)]"
            >
              {scheduleCron ? "Change" : "Set schedule"}
            </button>
            {scheduleCron ? (
              <button
                type="button"
                disabled={saving}
                onClick={() => save(null)}
                className="rounded-lg border border-[var(--seo-border)] px-3 py-1.5 text-xs font-medium text-[var(--seo-text)] hover:bg-[var(--seo-card-hover)] disabled:opacity-60"
              >
                Turn off
              </button>
            ) : null}
          </div>
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <select
            value={preset}
            onChange={(e) => setPreset(e.target.value)}
            className="rounded-lg border border-[var(--seo-border)] bg-[var(--seo-card-bg)] px-3 py-1.5 text-sm text-[var(--seo-heading)] outline-none focus:border-[var(--seo-accent)]"
          >
            {SCHEDULE_PRESETS.map((p) => (
              <option key={p.id} value={p.id}>
                {p.label}
              </option>
            ))}
          </select>
          {preset === "custom" ? (
            <input
              type="text"
              placeholder="0 0 * * *"
              value={customCron}
              onChange={(e) => setCustomCron(e.target.value)}
              className="rounded-lg border border-[var(--seo-border)] bg-[var(--seo-card-bg)] px-3 py-1.5 font-mono text-sm text-[var(--seo-heading)] outline-none focus:border-[var(--seo-accent)]"
            />
          ) : null}
          <button
            type="button"
            disabled={saving}
            onClick={applyPreset}
            className="rounded-lg btn-gradient px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-60"
          >
            {saving ? "Saving…" : "Save"}
          </button>
          <button
            type="button"
            disabled={saving}
            onClick={() => setEditing(false)}
            className="rounded-lg border border-[var(--seo-border)] px-3 py-1.5 text-xs font-medium text-[var(--seo-text)] hover:bg-[var(--seo-card-hover)]"
          >
            Cancel
          </button>
        </div>
      )}
      {error ? <p className="mt-2 text-xs text-[var(--seo-error)]">{error}</p> : null}
    </Card>
  );
}

function OverviewTab({
  status,
  themes,
  crawlId,
  onScheduleChange,
}: {
  status: CrawlStatus;
  themes: Record<string, ThemeReport> | null;
  crawlId: number;
  onScheduleChange: () => void;
}) {
  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Card className="flex items-center gap-4">
          <ScoreCircle score={status.healthScore ?? 0} size={64} label="Health Score" />
          <div className="text-xs text-[var(--seo-muted)]">
            % of crawled pages with zero error-severity issues
          </div>
        </Card>
        <Card className="flex items-center gap-4">
          <ScoreCircle score={status.seoScoreAvg ?? 0} size={64} label="SEO Score (avg)" />
          <div className="text-xs text-[var(--seo-muted)]">
            Weighted per-page score, averaged across the crawl
          </div>
        </Card>
      </div>

      <ScheduleCard
        crawlId={crawlId}
        scheduleCron={status.scheduleCron}
        nextRunAt={status.nextRunAt}
        onChange={onScheduleChange}
      />

      <Card>
        <h2 className="mb-3 text-sm font-semibold text-[var(--seo-heading)]">Thematic report</h2>
        {themes === null ? (
          <p className="text-xs text-[var(--seo-muted)]">Loading…</p>
        ) : Object.keys(themes).length === 0 ? (
          <p className="text-xs text-[var(--seo-muted)]">No issues found.</p>
        ) : (
          <div className="flex flex-col gap-2.5">
            {Object.entries(themes).map(([theme, report]) => (
              <div
                key={theme}
                className="flex flex-wrap items-center gap-2 border-b border-[var(--seo-border)] pb-2.5 last:border-0 last:pb-0"
              >
                <span className="min-w-[140px] text-sm font-medium text-[var(--seo-subheading)]">
                  {theme}
                </span>
                <span className="text-xs text-[var(--seo-muted)]">
                  {report.count} issue{report.count === 1 ? "" : "s"}
                </span>
                <div className="ml-auto flex gap-1.5">
                  {Object.entries(report.by_severity).map(([severity, count]) => (
                    <SeverityChip key={severity} severity={severity} count={count} />
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>

      <p className="text-xs text-[var(--seo-muted)]">
        Finished {formatDate(status.finishedAt)} · {status.pagesCrawled} pages crawled
      </p>
    </div>
  );
}

function PagesTab({
  crawlId,
  initialSearch,
  selectedId,
  onSelect,
}: {
  crawlId: number;
  initialSearch?: string;
  selectedId: number | null;
  onSelect: (row: PageRow) => void;
}) {
  // Fresh mount every time this tab becomes active (see the conditional
  // render below) picks up whatever initialSearch a "View page" jump set.
  const [search, setSearch] = useState(initialSearch ?? "");
  const [debouncedSearch, setDebouncedSearch] = useState(initialSearch ?? "");
  const [page, setPage] = useState(1);
  const pageSize = 25;

  useEffect(() => {
    const t = setTimeout(() => {
      setDebouncedSearch(search);
      setPage(1);
    }, 300);
    return () => clearTimeout(t);
  }, [search]);

  const { data, error, loading } = useCrawlListing<PagesResponse>("pages", crawlId, {
    search: debouncedSearch,
    page,
    pageSize,
  });
  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.pageSize)) : 1;

  return (
    <Card>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search URLs…"
          className="min-w-0 flex-1 rounded-lg border border-[var(--seo-border-strong)] bg-[var(--seo-card-bg)] px-2.5 py-1.5 text-sm text-[var(--seo-text)] outline-none focus:border-[var(--seo-accent)]"
        />
        {data ? (
          <span className="text-xs text-[var(--seo-muted)]">
            {data.total} page{data.total === 1 ? "" : "s"}
          </span>
        ) : null}
      </div>
      {error ? <p className="text-xs text-[var(--seo-error)]">{error}</p> : null}
      {loading && !data ? <p className="text-xs text-[var(--seo-muted)]">Loading…</p> : null}
      {data && data.pages.length === 0 ? (
        <p className="text-xs text-[var(--seo-muted)]">No pages found.</p>
      ) : null}
      {data && data.pages.length > 0 ? (
        <div className="max-h-[420px] overflow-auto">
          <table className="w-full border-collapse text-left text-sm">
            <thead>
              <tr className="sticky top-0 z-10">
                <th className={GRID_TH}>URL</th>
                <th className={GRID_TH}>Status</th>
                <th className={GRID_TH}>Title</th>
                <th className={GRID_TH}>Score</th>
                <th className={GRID_TH}>Issues</th>
                <th className={GRID_TH}>Crawled</th>
              </tr>
            </thead>
            <tbody>
              {data.pages.map((p) => {
                const isSelected = selectedId === p.id;
                const statusColor =
                  p.statusCode == null ? "var(--seo-muted)" : p.statusCode >= 400 ? "var(--seo-error)" : "var(--seo-success)";
                return (
                  <tr
                    key={p.id}
                    onClick={() => onSelect(p)}
                    className="cursor-pointer hover:bg-[var(--table-row-hover)]"
                    style={isSelected ? { backgroundColor: "var(--seo-accent-light)" } : undefined}
                  >
                    <td className={`${GRID_TD} max-w-xs truncate font-mono text-[var(--seo-heading)]`} title={p.url}>
                      {p.url}
                    </td>
                    <td className={GRID_TD}>
                      <span className="inline-flex items-center gap-1.5 tabular-nums">
                        <StatusDot color={statusColor} />
                        {p.statusCode ?? "-"}
                      </span>
                    </td>
                    <td className={`${GRID_TD} max-w-[220px] truncate`} title={p.title || ""}>
                      {p.title || "-"}
                    </td>
                    <td className={`${GRID_TD} tabular-nums`}>{p.seoScore != null ? Math.round(p.seoScore) : "-"}</td>
                    <td className={GRID_TD}>
                      <div className="flex flex-wrap items-center gap-2">
                        {Object.entries(p.issueCounts).length === 0 ? (
                          <span className="text-[var(--seo-success)]">0</span>
                        ) : (
                          Object.entries(p.issueCounts).map(([sev, count]) => (
                            <span key={sev} className="inline-flex items-center gap-1 tabular-nums" style={{ color: (SEVERITY_STYLE[sev] ?? SEVERITY_STYLE.notice).color }}>
                              <StatusDot color={(SEVERITY_STYLE[sev] ?? SEVERITY_STYLE.notice).color} />
                              {count}
                            </span>
                          ))
                        )}
                      </div>
                    </td>
                    <td className={`${GRID_TD} text-[var(--seo-muted)]`}>{formatDate(p.fetchedAt)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}
      {data ? <PaginationControls page={data.page} totalPages={totalPages} onChange={setPage} /> : null}
    </Card>
  );
}

function IssuesTab({
  crawlId,
  selectedId,
  onSelect,
}: {
  crawlId: number;
  selectedId: number | null;
  onSelect: (row: IssueRow) => void;
}) {
  const [severity, setSeverity] = useState("");
  const [category, setCategory] = useState("");
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [page, setPage] = useState(1);
  const pageSize = 25;

  useEffect(() => {
    const t = setTimeout(() => {
      setDebouncedSearch(search);
      setPage(1);
    }, 300);
    return () => clearTimeout(t);
  }, [search]);

  useEffect(() => {
    setPage(1);
  }, [severity, category]);

  const { data, error, loading } = useCrawlListing<IssuesResponse>("issues", crawlId, {
    severity: severity || undefined,
    category: category || undefined,
    search: debouncedSearch,
    page,
    pageSize,
  });
  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.pageSize)) : 1;

  return (
    <Card>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <div className="flex gap-1">
          {["", "error", "warning", "notice"].map((s) => {
            const isActive = severity === s;
            const style = s ? SEVERITY_STYLE[s] : { color: "var(--seo-text)", bg: "var(--seo-card-hover)" };
            return (
              <button
                key={s || "all"}
                type="button"
                onClick={() => setSeverity(s)}
                className="pill capitalize"
                style={{
                  color: isActive ? "#fff" : style.color,
                  backgroundColor: isActive ? "var(--seo-accent)" : style.bg,
                }}
              >
                {s || "All"}
              </button>
            );
          })}
        </div>
        <select
          value={category}
          onChange={(e) => setCategory(e.target.value)}
          className="rounded-lg border border-[var(--seo-border-strong)] bg-[var(--seo-card-bg)] px-2.5 py-1.5 text-sm text-[var(--seo-text)] outline-none focus:border-[var(--seo-accent)]"
        >
          <option value="">All categories</option>
          {(data?.categories || []).map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search issue type…"
          className="min-w-0 flex-1 rounded-lg border border-[var(--seo-border-strong)] bg-[var(--seo-card-bg)] px-2.5 py-1.5 text-sm text-[var(--seo-text)] outline-none focus:border-[var(--seo-accent)]"
        />
        {data ? (
          <span className="text-xs text-[var(--seo-muted)]">
            {data.total} issue{data.total === 1 ? "" : "s"}
          </span>
        ) : null}
      </div>
      {error ? <p className="text-xs text-[var(--seo-error)]">{error}</p> : null}
      {loading && !data ? <p className="text-xs text-[var(--seo-muted)]">Loading…</p> : null}
      {data && data.issues.length === 0 ? (
        <p className="text-xs text-[var(--seo-muted)]">No issues found.</p>
      ) : null}
      {data && data.issues.length > 0 ? (
        <div className="max-h-[420px] overflow-auto">
          <table className="w-full border-collapse text-left text-sm">
            <thead>
              <tr className="sticky top-0 z-10">
                <th className={GRID_TH}>Severity</th>
                <th className={GRID_TH}>Issue</th>
                <th className={GRID_TH}>Category</th>
                <th className={GRID_TH}>Impact</th>
                <th className={GRID_TH}>Page</th>
              </tr>
            </thead>
            <tbody>
              {data.issues.map((issue) => {
                const isSelected = selectedId === issue.id;
                const color = (SEVERITY_STYLE[issue.severity] ?? SEVERITY_STYLE.notice).color;
                return (
                  <tr
                    key={issue.id}
                    onClick={() => onSelect(issue)}
                    className="cursor-pointer hover:bg-[var(--table-row-hover)]"
                    style={isSelected ? { backgroundColor: "var(--seo-accent-light)" } : undefined}
                  >
                    <td className={GRID_TD}>
                      <span className="inline-flex items-center gap-1.5 capitalize" style={{ color }}>
                        <StatusDot color={color} />
                        {issue.severity}
                      </span>
                    </td>
                    <td className={`${GRID_TD} max-w-xs truncate text-[var(--seo-heading)]`} title={issue.issueType}>
                      {issue.issueType}
                    </td>
                    <td className={GRID_TD}>{issue.category}</td>
                    <td className={`${GRID_TD} tabular-nums`}>{issue.impactScore ?? "-"}</td>
                    <td
                      className={`${GRID_TD} max-w-[220px] truncate font-mono text-[var(--seo-muted)]`}
                      title={issue.pageUrl || undefined}
                    >
                      {issue.pageUrl || "Sitewide"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}
      {data ? <PaginationControls page={data.page} totalPages={totalPages} onChange={setPage} /> : null}
    </Card>
  );
}

const LINK_TYPE_LABELS: Record<string, string> = {
  internal: "Internal",
  external: "External",
  mailto: "Mailto",
  tel: "Tel",
  anchor: "Anchor",
  javascript: "JS",
};

function LinksTab({
  crawlId,
  selectedId,
  onSelect,
}: {
  crawlId: number;
  selectedId: number | null;
  onSelect: (row: LinkRow) => void;
}) {
  const [linkType, setLinkType] = useState("");
  const [brokenOnly, setBrokenOnly] = useState(false);
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [page, setPage] = useState(1);
  const pageSize = 25;

  useEffect(() => {
    const t = setTimeout(() => {
      setDebouncedSearch(search);
      setPage(1);
    }, 300);
    return () => clearTimeout(t);
  }, [search]);

  useEffect(() => {
    setPage(1);
  }, [linkType, brokenOnly]);

  const { data, error, loading } = useCrawlListing<LinksResponse>("links", crawlId, {
    linkType: linkType || undefined,
    brokenOnly: brokenOnly || undefined,
    search: debouncedSearch,
    page,
    pageSize,
  });
  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.pageSize)) : 1;
  const linkTypeCounts = data?.linkTypeCounts || {};
  const allTypesCount = Object.values(linkTypeCounts).reduce((a, b) => a + b, 0);

  return (
    <Card>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <div className="flex flex-wrap gap-1">
          <button
            type="button"
            onClick={() => setLinkType("")}
            className="pill"
            style={{
              color: linkType === "" ? "#fff" : "var(--seo-text)",
              backgroundColor: linkType === "" ? "var(--seo-accent)" : "var(--seo-card-hover)",
            }}
          >
            All ({allTypesCount})
          </button>
          {Object.keys(LINK_TYPE_LABELS)
            .filter((t) => linkTypeCounts[t])
            .map((t) => {
              const isActive = linkType === t;
              return (
                <button
                  key={t}
                  type="button"
                  onClick={() => setLinkType(t)}
                  className="pill"
                  style={{
                    color: isActive ? "#fff" : "var(--seo-text)",
                    backgroundColor: isActive ? "var(--seo-accent)" : "var(--seo-card-hover)",
                  }}
                >
                  {LINK_TYPE_LABELS[t]} ({linkTypeCounts[t]})
                </button>
              );
            })}
          <button
            type="button"
            onClick={() => setBrokenOnly((v) => !v)}
            className="pill"
            style={{
              color: brokenOnly ? "#fff" : "var(--seo-error)",
              backgroundColor: brokenOnly ? "var(--seo-error)" : "var(--seo-error-bg)",
            }}
          >
            Broken only
          </button>
        </div>
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search URL or anchor text…"
          className="min-w-0 flex-1 rounded-lg border border-[var(--seo-border-strong)] bg-[var(--seo-card-bg)] px-2.5 py-1.5 text-sm text-[var(--seo-text)] outline-none focus:border-[var(--seo-accent)]"
        />
        {data ? (
          <span className="text-xs text-[var(--seo-muted)]">
            {data.total} link{data.total === 1 ? "" : "s"}
          </span>
        ) : null}
      </div>
      {error ? <p className="text-xs text-[var(--seo-error)]">{error}</p> : null}
      {loading && !data ? <p className="text-xs text-[var(--seo-muted)]">Loading…</p> : null}
      {data && data.links.length === 0 ? (
        <p className="text-xs text-[var(--seo-muted)]">No links found.</p>
      ) : null}
      {data && data.links.length > 0 ? (
        <div className="max-h-[420px] overflow-auto">
          <table className="w-full border-collapse text-left text-sm">
            <thead>
              <tr className="sticky top-0 z-10">
                <th className={GRID_TH}>Target URL</th>
                <th className={GRID_TH}>Type</th>
                <th className={GRID_TH}>Anchor text</th>
                <th className={GRID_TH}>Location</th>
                <th className={GRID_TH}>Follow</th>
                <th className={GRID_TH}>Status</th>
                <th className={GRID_TH}>From page</th>
              </tr>
            </thead>
            <tbody>
              {data.links.map((l) => {
                const isSelected = selectedId === l.id;
                const followColor = l.isNofollow ? "var(--seo-warning)" : "var(--seo-success)";
                const statusColor = l.isBroken ? "var(--seo-error)" : l.statusCode != null ? "var(--seo-success)" : "var(--seo-muted)";
                return (
                  <tr
                    key={l.id}
                    onClick={() => onSelect(l)}
                    className="cursor-pointer hover:bg-[var(--table-row-hover)]"
                    style={isSelected ? { backgroundColor: "var(--seo-accent-light)" } : undefined}
                  >
                    <td className={`${GRID_TD} max-w-xs truncate font-mono text-[var(--seo-heading)]`} title={l.targetUrl}>
                      {l.targetUrl}
                    </td>
                    <td className={GRID_TD}>{LINK_TYPE_LABELS[l.linkType] || l.linkType}</td>
                    <td className={`${GRID_TD} max-w-[200px] truncate`} title={l.anchorText || ""}>
                      {l.anchorText || "-"}
                    </td>
                    <td className={GRID_TD}>{l.domLocation || "-"}</td>
                    <td className={GRID_TD}>
                      <span className="inline-flex items-center gap-1.5" style={{ color: followColor }}>
                        <StatusDot color={followColor} />
                        {l.isNofollow ? "Nofollow" : "Dofollow"}
                      </span>
                    </td>
                    <td className={`${GRID_TD} tabular-nums`}>
                      <span className="inline-flex items-center gap-1.5" style={{ color: statusColor }}>
                        <StatusDot color={statusColor} />
                        {l.isBroken ? `Broken (${l.statusCode ?? "-"})` : (l.statusCode ?? "Not checked")}
                      </span>
                    </td>
                    <td
                      className={`${GRID_TD} max-w-[200px] truncate font-mono text-[var(--seo-muted)]`}
                      title={l.pageUrl || undefined}
                    >
                      {l.pageUrl || "-"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}
      {data ? <PaginationControls page={data.page} totalPages={totalPages} onChange={setPage} /> : null}
    </Card>
  );
}

function ScoreDelta({ label, delta }: { label: string; delta: number | null | undefined }) {
  const color =
    delta == null || delta === 0 ? "var(--seo-muted)" : delta > 0 ? "var(--seo-success)" : "var(--seo-error)";
  return (
    <div>
      <div className="text-xs text-[var(--seo-muted)]">{label}</div>
      <div className="text-lg font-semibold tabular-nums" style={{ color }}>
        {delta == null ? "-" : `${delta > 0 ? "+" : ""}${delta.toFixed(1)}`}
      </div>
    </div>
  );
}

function DiffIssueList({ title, issues }: { title: string; issues: DiffIssue[] }) {
  return (
    <div>
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-[var(--seo-muted)]">
        {title} ({issues.length})
      </h3>
      {issues.length === 0 ? (
        <p className="text-xs text-[var(--seo-muted)]">None.</p>
      ) : (
        <ul className="flex flex-col gap-1.5">
          {issues.map((i, idx) => (
            <li key={idx} className="text-xs">
              <span className="font-medium text-[var(--seo-heading)]">{i.issue_type}</span>
              <span className="ml-1.5 font-mono text-[var(--seo-muted)]">{i.url ?? "Sitewide"}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function DiffPageList({ title, pages, color }: { title: string; pages: DiffPage[]; color: string }) {
  return (
    <div>
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-[var(--seo-muted)]">
        {title} ({pages.length})
      </h3>
      {pages.length === 0 ? (
        <p className="text-xs text-[var(--seo-muted)]">None.</p>
      ) : (
        <ul className="flex flex-col gap-1.5">
          {pages.map((p, idx) => (
            <li key={idx} className="text-xs">
              <span className="font-mono text-[var(--seo-text)]">{p.url}</span>
              <span className="ml-1.5 tabular-nums" style={{ color }}>
                {Math.round(p.old_score)} → {Math.round(p.new_score)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function CompareTab({ crawlId, rootUrl }: { crawlId: number; rootUrl: string | null }) {
  const [compareToId, setCompareToId] = useState<number | null>(null);
  const [compare, setCompare] = useState<CompareResponse | null>(null);
  const [candidates, setCandidates] = useState<CrawlListItem[]>([]);
  const [trend, setTrend] = useState<TrendPoint[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    postCrawlsAction<{ crawls: CrawlListItem[] }>({ action: "list" })
      .then((data) => {
        setCandidates(
          data.crawls.filter((c) => c.rootUrl === rootUrl && c.id !== crawlId && c.status === "completed")
        );
      })
      .catch(() => {
        /* candidate picker is a convenience; the default comparison still works without it */
      });
  }, [crawlId, rootUrl]);

  useEffect(() => {
    postCrawlsAction<{ trend: TrendPoint[] }>({ action: "trend", crawlId })
      .then((data) => setTrend(data.trend))
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load score trend."));
  }, [crawlId]);

  useEffect(() => {
    setLoading(true);
    const body: Record<string, unknown> = { action: "compare", crawlId };
    if (compareToId != null) body.compareToId = compareToId;
    postCrawlsAction<CompareResponse>(body)
      .then((data) => {
        setCompare(data);
        if (data.available && data.compareToId != null && compareToId == null) {
          setCompareToId(data.compareToId);
        }
        setError(null);
      })
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load comparison."))
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [crawlId, compareToId]);

  const trendData = (trend || []).map((t) => ({ ...t, label: formatDate(t.finished_at) }));

  return (
    <div className="flex flex-col gap-4">
      {error ? <p className="text-xs text-[var(--seo-error)]">{error}</p> : null}

      <Card>
        <h2 className="mb-3 text-sm font-semibold text-[var(--seo-heading)]">Score trend</h2>
        {trend === null ? (
          <p className="text-xs text-[var(--seo-muted)]">Loading…</p>
        ) : trend.length < 2 ? (
          <p className="text-xs text-[var(--seo-muted)]">Not enough crawl history yet for a trend line.</p>
        ) : (
          <ResponsiveContainer width="100%" height={220}>
            <LineChart data={trendData}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--seo-border)" />
              <XAxis dataKey="label" tick={{ fontSize: 11, fill: "var(--seo-muted)" }} />
              <YAxis domain={[0, 100]} tick={{ fontSize: 11, fill: "var(--seo-muted)" }} />
              <Tooltip contentStyle={CHART_TOOLTIP_STYLE} labelStyle={CHART_TOOLTIP_LABEL_STYLE} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Line type="monotone" dataKey="health_score" name="Health Score" stroke="#6366F1" strokeWidth={2} dot={{ r: 3 }} />
              <Line type="monotone" dataKey="seo_score_avg" name="SEO Score" stroke="#10B981" strokeWidth={2} dot={{ r: 3 }} />
            </LineChart>
          </ResponsiveContainer>
        )}
      </Card>

      <Card>
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-sm font-semibold text-[var(--seo-heading)]">Compare against</h2>
          {candidates.length > 0 ? (
            <select
              value={compareToId ?? ""}
              onChange={(e) => setCompareToId(Number(e.target.value))}
              className="rounded-lg border border-[var(--seo-border-strong)] bg-[var(--seo-card-bg)] px-2.5 py-1.5 text-sm text-[var(--seo-text)] outline-none focus:border-[var(--seo-accent)]"
            >
              {candidates.map((c) => (
                <option key={c.id} value={c.id}>
                  Crawl #{c.id} - {formatDate(c.finishedAt)}
                </option>
              ))}
            </select>
          ) : null}
        </div>

        {loading && !compare ? <p className="text-xs text-[var(--seo-muted)]">Loading…</p> : null}

        {compare && !compare.available ? (
          <p className="text-xs text-[var(--seo-muted)]">
            Nothing to compare yet - run this crawl again to see what changed.
          </p>
        ) : null}

        {compare?.available && compare.diff ? (
          <div className="flex flex-col gap-4">
            <div className="grid grid-cols-2 gap-4">
              <ScoreDelta label="Health Score change" delta={compare.diff.healthScoreDelta} />
              <ScoreDelta label="SEO Score change" delta={compare.diff.seoScoreAvgDelta} />
            </div>

            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <DiffIssueList title="New issues" issues={compare.diff.newIssues} />
              <DiffIssueList title="Fixed issues" issues={compare.diff.fixedIssues} />
            </div>

            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <DiffPageList title="Regressed pages" pages={compare.diff.regressedPages} color="var(--seo-error)" />
              <DiffPageList title="Improved pages" pages={compare.diff.improvedPages} color="var(--seo-success)" />
            </div>
          </div>
        ) : null}
      </Card>
    </div>
  );
}

function humanizeType(t: string): string {
  return t.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function MiniStat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-[var(--seo-radius)] border border-[var(--seo-border)] bg-[var(--seo-card-bg)] px-3.5 py-3">
      <div className="text-xl font-bold tabular-nums text-[var(--seo-heading)]">{value.toLocaleString()}</div>
      <div className="text-[11px] text-[var(--seo-muted)]">{label}</div>
    </div>
  );
}

function SiteIssueCard({ issue }: { issue: SiteIssue }) {
  const style = SEVERITY_STYLE[issue.severity] ?? SEVERITY_STYLE.notice;
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-[var(--seo-radius)] border border-[var(--seo-border)] bg-[var(--seo-card-bg)]">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-3 px-4 py-3 text-left"
      >
        <span className="inline-flex items-center gap-1.5 text-xs font-semibold capitalize" style={{ color: style.color }}>
          <StatusDot color={style.color} />
          {issue.severity}
        </span>
        <span className="text-sm font-semibold text-[var(--seo-heading)]">{humanizeType(issue.issueType)}</span>
        <span className="hidden text-xs text-[var(--seo-muted)] sm:inline">{issue.category}</span>
        <span className="ml-auto shrink-0 text-xs tabular-nums text-[var(--seo-muted)]">
          {issue.affectedCount} URL{issue.affectedCount === 1 ? "" : "s"}
        </span>
      </button>
      {open ? (
        <div className="flex flex-col gap-2 border-t border-[var(--seo-border)] px-4 py-3 text-[13px] text-[var(--seo-text)]">
          <div><span className="font-semibold text-[var(--seo-subheading)]">What: </span>{issue.what}</div>
          <div><span className="font-semibold text-[var(--seo-subheading)]">Why it matters: </span>{issue.why}</div>
          <div><span className="font-semibold text-[var(--seo-subheading)]">How to fix: </span>{issue.fix}</div>
          {issue.affectedUrls.length > 0 ? (
            <div>
              <span className="font-semibold text-[var(--seo-subheading)]">Affected pages:</span>
              <ul className="mt-1 flex flex-col gap-0.5">
                {issue.affectedUrls.slice(0, 8).map((u) => (
                  <li key={u} className="truncate font-mono text-xs text-[var(--seo-muted)]" title={u}>{u}</li>
                ))}
                {issue.affectedUrls.length > 8 ? (
                  <li className="text-xs text-[var(--seo-muted)]">+{issue.affectedUrls.length - 8} more</li>
                ) : null}
              </ul>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function SitewideTab({ crawlId }: { crawlId: number }) {
  const [sitewide, setSitewide] = useState<SitewideResponse | null>(null);
  const [graph, setGraph] = useState<CrawlGraphResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    Promise.all([
      postAnalyzeAction<SitewideResponse>("sitewide", crawlId),
      postAnalyzeAction<CrawlGraphResponse>("crawl-graph", crawlId),
    ])
      .then(([sw, g]) => {
        if (!cancelled) { setSitewide(sw); setGraph(g); }
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "Failed to run analysis.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [crawlId]);

  if (loading) {
    return <Card><p className="text-xs text-[var(--seo-muted)]">Running site-wide analysis…</p></Card>;
  }
  if (error) {
    return <Card><p className="text-xs text-[var(--seo-error)]">{error}</p></Card>;
  }

  const issues = sitewide?.issues ?? [];
  const depthIssues = graph?.issues ?? [];
  const perDepth = graph ? Object.entries(graph.pagesPerDepth) : [];
  const maxPerDepth = perDepth.length ? Math.max(...perDepth.map(([, c]) => c)) : 1;

  return (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <MiniStat label="Pages analyzed" value={sitewide?.pageCount ?? 0} />
        <MiniStat label="Links seen" value={sitewide?.linkCount ?? 0} />
        <MiniStat label="Site-wide issues" value={sitewide?.issueCount ?? 0} />
        <MiniStat label="Max click depth" value={graph?.maxDepth ?? 0} />
      </div>

      <Card>
        <h3 className="mb-3 text-sm font-semibold text-[var(--seo-heading)]">Site-wide issues</h3>
        {issues.length === 0 ? (
          <p className="text-xs text-[var(--seo-muted)]">
            No site-wide issues found - no duplicate titles, descriptions, orphan pages, redirect loops,
            or broken internal links across the crawl.
          </p>
        ) : (
          <div className="flex flex-col gap-2">
            {issues.map((iss, i) => <SiteIssueCard key={`${iss.issueType}-${i}`} issue={iss} />)}
          </div>
        )}
      </Card>

      <Card>
        <h3 className="mb-1 text-sm font-semibold text-[var(--seo-heading)]">Crawl depth</h3>
        <p className="mb-3 text-xs text-[var(--seo-muted)]">
          How many clicks each page sits from the homepage - deep pages are harder for users and search engines to reach.
        </p>
        {graph ? (
          <>
            <div className="flex flex-col gap-1.5">
              {perDepth
                .sort((a, b) => Number(a[0]) - Number(b[0]))
                .map(([depth, count]) => (
                  <div key={depth} className="flex items-center gap-2 text-xs">
                    <span className="w-16 shrink-0 text-[var(--seo-muted)]">Depth {depth}</span>
                    <div className="h-4 flex-1 overflow-hidden rounded bg-[var(--seo-card-hover)]">
                      <div className="h-4 rounded" style={{ width: `${(count / maxPerDepth) * 100}%`, background: "var(--seo-gradient)" }} />
                    </div>
                    <span className="w-10 shrink-0 text-right tabular-nums text-[var(--seo-text)]">{count}</span>
                  </div>
                ))}
            </div>
            <div className="mt-3 flex flex-wrap gap-x-6 gap-y-1 text-xs">
              <span><span className="text-[var(--seo-muted)]">Average depth: </span><span className="font-semibold tabular-nums text-[var(--seo-heading)]">{graph.avgDepth}</span></span>
              <span><span className="text-[var(--seo-muted)]">Reachable pages: </span><span className="font-semibold tabular-nums text-[var(--seo-heading)]">{graph.reachableCount}</span></span>
            </div>
            {graph.unreachableUrls.length > 0 ? (
              <div className="mt-3">
                <p className="text-xs font-semibold text-[var(--seo-subheading)]">
                  Orphan / unreachable pages ({graph.unreachableUrls.length})
                </p>
                <ul className="mt-1 flex flex-col gap-0.5">
                  {graph.unreachableUrls.slice(0, 8).map((u) => (
                    <li key={u} className="truncate font-mono text-xs text-[var(--seo-muted)]" title={u}>{u}</li>
                  ))}
                  {graph.unreachableUrls.length > 8 ? (
                    <li className="text-xs text-[var(--seo-muted)]">+{graph.unreachableUrls.length - 8} more</li>
                  ) : null}
                </ul>
              </div>
            ) : null}
            {depthIssues.length > 0 ? (
              <div className="mt-3 flex flex-col gap-2">
                {depthIssues.map((iss, i) => <SiteIssueCard key={`depth-${i}`} issue={iss} />)}
              </div>
            ) : null}
          </>
        ) : null}
      </Card>
    </div>
  );
}

function CrawlDetailInner() {
  const params = useParams();
  const router = useRouter();
  const searchParams = useSearchParams();
  const crawlId = Number(params.id);

  const rawTab = searchParams.get("tab");
  const activeTab: Tab = (TABS as readonly string[]).includes(rawTab || "") ? (rawTab as Tab) : "Overview";

  function setActiveTab(tab: Tab) {
    const next = new URLSearchParams(searchParams.toString());
    next.set("tab", tab);
    router.replace(`/site-crawls/${crawlId}?${next.toString()}`);
  }

  const [status, setStatus] = useState<CrawlStatus | null>(null);
  // Mirror the latest status in a ref so the poll interval's callback (which
  // closes over the first render's `status`) can read the current value and
  // stop once the crawl finishes, without re-creating the interval.
  const statusRef = useRef<CrawlStatus | null>(null);
  const [themes, setThemes] = useState<Record<string, ThemeReport> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pollCount, setPollCount] = useState(0);
  const themesLoadedFor = useRef<number | null>(null);

  // Unfiltered totals for the tab-strip counts (Pages/Issues/Links), fetched
  // once when the crawl completes - independent of whatever filters are
  // active inside each tab, so "Issues (58)" always reads the full count.
  const [tabCounts, setTabCounts] = useState<{ pages: number | null; issues: number | null; links: number | null }>({
    pages: null,
    issues: null,
    links: null,
  });
  const [selectedRow, setSelectedRow] = useState<SelectedRow | null>(null);
  const [pagesInitialSearch, setPagesInitialSearch] = useState<string | undefined>(undefined);

  function selectTab(tab: Tab) {
    setSelectedRow(null);
    setActiveTab(tab);
  }

  function viewPage(url: string) {
    setPagesInitialSearch(url);
    selectTab("Pages");
  }

  useEffect(() => {
    if (!Number.isFinite(crawlId)) return;
    let cancelled = false;

    async function poll() {
      try {
        const data = await postCrawlsAction<CrawlStatus>({ action: "status", crawlId });
        if (cancelled) return;
        statusRef.current = data;
        setStatus(data);
        setError(null);
        setPollCount((c) => c + 1);

        if (data.status === "completed" && themesLoadedFor.current !== crawlId) {
          themesLoadedFor.current = crawlId;
          const themeData = await postCrawlsAction<{ themes: Record<string, ThemeReport> }>({
            action: "thematic",
            crawlId,
          });
          if (!cancelled) setThemes(themeData.themes);

          const [pagesTotal, issuesTotal, linksTotal] = await Promise.all([
            postCrawlsAction<PagesResponse>({ action: "pages", crawlId, page: 1, pageSize: 1 }),
            postCrawlsAction<IssuesResponse>({ action: "issues", crawlId, page: 1, pageSize: 1 }),
            postCrawlsAction<LinksResponse>({ action: "links", crawlId, page: 1, pageSize: 1 }),
          ]);
          if (!cancelled) {
            setTabCounts({ pages: pagesTotal.total, issues: issuesTotal.total, links: linksTotal.total });
          }
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load crawl status.");
      }
    }

    poll();
    const interval = setInterval(() => {
      // Read the live status from the ref (not the stale closure) so the poll
      // actually stops once the crawl reaches a terminal state.
      if (statusRef.current && FINISHED_STATUSES.has(statusRef.current.status)) {
        clearInterval(interval);
        return;
      }
      poll();
    }, POLL_INTERVAL_MS);

    return () => {
      cancelled = true;
      clearInterval(interval);
    };
    // Intentionally keyed on crawlId only; the latest status is read from
    // statusRef inside the interval, so it needn't be a dependency.
  }, [crawlId]);

  async function refetchStatus() {
    try {
      const data = await postCrawlsAction<CrawlStatus>({ action: "status", crawlId });
      setStatus(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load crawl status.");
    }
  }

  // Pause a running crawl (the worker stops cleanly between batches) or resume a
  // paused/failed one (re-queued; the worker continues from persisted pages).
  // Requires the always-on worker (M2 T2.1) to actually act on it.
  const [pauseBusy, setPauseBusy] = useState(false);
  async function pauseResume(action: "pause" | "resume") {
    setPauseBusy(true);
    try {
      await postCrawlsAction({ action, crawlId });
      await refetchStatus();
    } catch (err) {
      setError(err instanceof Error ? err.message : `Failed to ${action} the crawl.`);
    } finally {
      setPauseBusy(false);
    }
  }

  if (!Number.isFinite(crawlId)) {
    return <p className="text-sm text-[var(--seo-error)]">Invalid crawl id.</p>;
  }

  return (
    <div>
      <PageHeader
        icon={<GlobeIcon size={18} />}
        title={status?.rootUrl ?? `Crawl #${crawlId}`}
        subtitle={status ? `Status: ${status.status}` : "Loading…"}
        actions={
          <div className="flex items-center gap-2">
            {SERVER_CRAWL && status && (status.status === "running" || status.status === "queued") ? (
              <button
                type="button"
                onClick={() => pauseResume("pause")}
                disabled={pauseBusy}
                className="rounded-lg border border-[var(--seo-border-strong)] px-3 py-1.5 text-sm font-medium text-[var(--seo-text)] hover:bg-[var(--seo-card-hover)] disabled:opacity-50"
              >
                Pause
              </button>
            ) : null}
            {SERVER_CRAWL && status && (status.status === "paused" || status.status === "failed") ? (
              <button
                type="button"
                onClick={() => pauseResume("resume")}
                disabled={pauseBusy}
                className="rounded-lg btn-gradient px-3 py-1.5 text-sm font-semibold text-white disabled:opacity-50"
              >
                Resume
              </button>
            ) : null}
            <button
              type="button"
              onClick={() => router.push("/site-crawls")}
              className="rounded-lg border border-[var(--seo-border)] px-3 py-1.5 text-sm font-medium text-[var(--seo-text)] hover:bg-[var(--seo-card-hover)]"
            >
              ← All crawls
            </button>
          </div>
        }
      />

      {error ? <p className="mb-4 text-sm text-[var(--seo-error)]">{error}</p> : null}

      {!status ? null : status.status === "failed" ? (
        <Card>
          <div className="text-sm font-semibold text-[var(--seo-error)]">This crawl failed.</div>
          <p className="mt-1 text-xs text-[var(--seo-muted)]">
            {status.pagesCrawled > 0
              ? `${status.pagesCrawled} page(s) were crawled before it stopped. Use Resume to continue from where it left off (requires the worker to be running).`
              : "Check the worker process logs (`python -m worker`) for the error."}
          </p>
        </Card>
      ) : status.status === "paused" ? (
        <Card>
          <div className="text-sm font-semibold text-[var(--seo-heading)]">
            Paused - {status.pagesCrawled} page{status.pagesCrawled === 1 ? "" : "s"} crawled so far
          </div>
          <p className="mt-1 text-xs text-[var(--seo-muted)]">
            Use Resume to continue from where it left off (already-crawled pages are kept).
          </p>
        </Card>
      ) : status.status !== "completed" ? (
        <Card>
          <div className="flex items-center gap-3">
            <span className="h-2.5 w-2.5 shrink-0 animate-pulse rounded-full bg-[var(--seo-accent)]" />
            <div>
              <div className="text-sm font-semibold text-[var(--seo-heading)]">
                {status.status === "queued" ? "Queued" : "Crawling…"} - {status.pagesCrawled} page
                {status.pagesCrawled === 1 ? "" : "s"} crawled
                {status.pagesTotalEstimate ? ` of up to ${status.pagesTotalEstimate}` : ""}
              </div>
              {status.status === "queued" && pollCount >= 3 ? (
                <p className="mt-1 text-xs text-[var(--seo-muted)]">
                  Still queued after a few checks - make sure the worker process (
                  <code className="font-mono">python -m worker</code>) is running.
                </p>
              ) : null}
            </div>
          </div>
        </Card>
      ) : (
        <>
          {/* Pages/Issues browsing only makes sense once the crawl has
              produced final results - matches the existing gate for the
              Overview scores/thematic report, not a new restriction. */}
          <SpreadsheetTabBar
            tabs={[
              { key: "Overview", label: "Overview" },
              { key: "Pages", label: "Pages", count: tabCounts.pages },
              { key: "Issues", label: "Issues", count: tabCounts.issues },
              { key: "Site-wide", label: "Site-wide" },
              { key: "Links", label: "Links", count: tabCounts.links },
              { key: "Compare", label: "Compare" },
            ]}
            active={activeTab}
            onChange={selectTab}
          />
          {activeTab === "Overview" ? (
            <OverviewTab status={status} themes={themes} crawlId={crawlId} onScheduleChange={refetchStatus} />
          ) : null}
          {activeTab === "Pages" ? (
            <div className="flex flex-col gap-3">
              <PagesTab
                crawlId={crawlId}
                initialSearch={pagesInitialSearch}
                selectedId={selectedRow?.type === "page" ? selectedRow.data.id : null}
                onSelect={(row) => setSelectedRow({ type: "page", data: row })}
              />
              <DetailPanel crawlId={crawlId} selected={selectedRow} onViewPage={viewPage} />
            </div>
          ) : null}
          {activeTab === "Issues" ? (
            <div className="flex flex-col gap-3">
              <IssuesTab
                crawlId={crawlId}
                selectedId={selectedRow?.type === "issue" ? selectedRow.data.id : null}
                onSelect={(row) => setSelectedRow({ type: "issue", data: row })}
              />
              <DetailPanel crawlId={crawlId} selected={selectedRow} onViewPage={viewPage} />
            </div>
          ) : null}
          {activeTab === "Site-wide" ? <SitewideTab crawlId={crawlId} /> : null}
          {activeTab === "Links" ? (
            <div className="flex flex-col gap-3">
              <LinksTab
                crawlId={crawlId}
                selectedId={selectedRow?.type === "link" ? selectedRow.data.id : null}
                onSelect={(row) => setSelectedRow({ type: "link", data: row })}
              />
              <DetailPanel crawlId={crawlId} selected={selectedRow} onViewPage={viewPage} />
            </div>
          ) : null}
          {activeTab === "Compare" ? <CompareTab crawlId={crawlId} rootUrl={status.rootUrl} /> : null}
        </>
      )}
    </div>
  );
}

export default function CrawlDetailPage() {
  // useSearchParams() (inside CrawlDetailInner) must have a <Suspense> ancestor
  // under the App Router, otherwise the whole route opts out of static rendering.
  return (
    <Suspense fallback={null}>
      <CrawlDetailInner />
    </Suspense>
  );
}
