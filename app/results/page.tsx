"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { useAudit } from "@/lib/state/AuditContext";
import { affectedHref, Card, EmptyState, looksLikeImage, looksLikeUrl, PageHeader, ScoreBadge, ScoreCircle, StatusPill } from "@/components/ui";
import { ListChecksIcon } from "@/components/icons";
import { ExportBar } from "@/components/ExportBar";
import { AiSummaryCard } from "@/components/AiSummaryCard";
import { allIssuesOf, avgScore, issuesByTitle, type AggregatedIssue } from "@/lib/aggregate";
import { difficultyBreakdown } from "@/lib/difficulty";
import { downloadCsv, severityColor } from "@/lib/format";
import { categorizeUrl, categoryColor } from "@/lib/pageCategory";
import type { AuditResult } from "@/lib/types";

function pathnameOf(url: string): string {
  try {
    return new URL(url).pathname || url;
  } catch {
    return url;
  }
}

// Upper bound on affected-element rows rendered per expanded failing check. A
// sitewide audit can attach hundreds of offending elements to one issue; the
// list only needs to be a scannable sample, so we cap and show "+N more".
const FAILING_AFFECTED_CAP = 20;

/**
 * One row in the sitewide "Top failing checks" list: the issue title + a
 * "N pages" pill. It expands to show, when the backend attached them, the exact
 * offending elements as "on <page>: <value> - <detail>" (the page still links
 * into that page's detail via onOpenUrl, and URL/image values open in a new
 * tab). When no per-element data is present it falls back to the affected-page
 * URL list.
 */
function FailingIssueRow({
  issue,
  onOpenUrl,
}: {
  issue: AggregatedIssue;
  onOpenUrl: (url: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [showAll, setShowAll] = useState(false);
  const color = severityColor(issue.severity).text;
  const affected = issue.affected || [];
  const hasAffected = affected.length > 0;
  const visibleAffected = showAll ? affected : affected.slice(0, FAILING_AFFECTED_CAP);
  const hiddenAffected = affected.length - visibleAffected.length;

  return (
    <div className="text-sm">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between gap-3 text-left"
        aria-expanded={open}
      >
        <span className="flex min-w-0 items-center gap-2">
          <span className={`shrink-0 text-xs text-[var(--seo-muted)] transition-transform ${open ? "rotate-90" : ""}`}>▸</span>
          <span className="truncate text-[var(--seo-text)]" style={{ borderLeft: `3px solid ${color}`, paddingLeft: 8 }}>
            {issue.issue}
          </span>
        </span>
        <span className="shrink-0 rounded-full bg-[var(--seo-card-hover)] px-2 py-0.5 text-xs font-medium text-[var(--seo-text-light)]">
          {issue.count} {issue.count === 1 ? "page" : "pages"}
        </span>
      </button>
      {open ? (
        hasAffected ? (
          <ul className="ml-6 mt-1 flex flex-col gap-1 border-l border-[var(--seo-border)] pl-3">
            {visibleAffected.map((a, i) => {
              const linkable = looksLikeUrl(a.value) || looksLikeImage(a.value);
              const href = linkable ? affectedHref(a.value, a.url) : null;
              return (
                <li key={`${a.url}-${a.value}-${i}`} className="flex min-w-0 flex-wrap items-baseline gap-x-1.5 gap-y-0.5 text-xs">
                  <span className="text-[var(--seo-muted)]">on</span>
                  <button
                    type="button"
                    onClick={() => onOpenUrl(a.url)}
                    className="max-w-[16rem] truncate text-left text-[var(--seo-accent)] hover:underline"
                    title={a.url}
                  >
                    {pathnameOf(a.url)}
                  </button>
                  <span className="text-[var(--seo-muted)]">:</span>
                  {href ? (
                    <a
                      href={href}
                      target="_blank"
                      rel="noopener noreferrer"
                      title={a.value}
                      className="min-w-0 max-w-[22rem] truncate font-mono text-[var(--seo-accent)] hover:underline"
                    >
                      {a.value}
                    </a>
                  ) : (
                    <span title={a.value} className="min-w-0 max-w-[22rem] truncate font-mono text-[var(--seo-text)]">
                      {a.value}
                    </span>
                  )}
                  {a.detail ? <span className="text-[var(--seo-muted)]">- {a.detail}</span> : null}
                </li>
              );
            })}
            {hiddenAffected > 0 ? (
              <li>
                <button
                  type="button"
                  onClick={() => setShowAll(true)}
                  className="text-[11px] font-medium text-[var(--seo-accent)] hover:underline"
                >
                  +{hiddenAffected} more
                </button>
              </li>
            ) : null}
          </ul>
        ) : (
          <ul className="ml-6 mt-1 flex flex-col gap-0.5 border-l border-[var(--seo-border)] pl-3">
            {issue.urls.map((u) => (
              <li key={u}>
                <button
                  type="button"
                  onClick={() => onOpenUrl(u)}
                  className="truncate text-left text-xs text-[var(--seo-accent)] hover:underline"
                  title={u}
                >
                  {pathnameOf(u)}
                </button>
              </li>
            ))}
          </ul>
        )
      ) : null}
    </div>
  );
}

/** Compact Easy/Medium/Hard fix-effort counts for a result's issues. */
function EffortChips({ result }: { result: AuditResult }) {
  const b = difficultyBreakdown(result.all_issues || []);
  if (b.Easy + b.Medium + b.Hard === 0) {
    return <span className="text-xs text-[var(--seo-success)]">None</span>;
  }
  const chip = (n: number, label: string, color: string) =>
    n > 0 ? (
      <span className="rounded px-1.5 py-0.5 text-xs font-medium" style={{ color, backgroundColor: "var(--seo-card-hover)" }} title={`${n} ${label} fix${n > 1 ? "es" : ""}`}>
        {n} {label}
      </span>
    ) : null;
  return (
    <span className="flex flex-wrap items-center gap-1">
      {chip(b.Easy, "easy", "var(--seo-success)")}
      {chip(b.Medium, "med", "var(--seo-warning)")}
      {chip(b.Hard, "hard", "var(--seo-error)")}
    </span>
  );
}

/** Page-category badge for the Results "Type" column (Course/Blog/Topic/…). */
function TypeBadge({ url, auditType }: { url: string; auditType?: string }) {
  const category = categorizeUrl(url, auditType);
  const c = categoryColor(category);
  return (
    <span className="pill" style={{ color: c.text, backgroundColor: c.bg }}>
      {category}
    </span>
  );
}

/** One result row in the flat Results table. */
function ResultRow({ r, onOpen }: { r: AuditResult; onOpen: (r: AuditResult) => void }) {
  const cl = r.technical_audit_checklist?.summary;
  const top = worstIssue(r);
  return (
    <tr
      onClick={() => onOpen(r)}
      className="cursor-pointer border-b border-[var(--table-row-border)] last:border-0 hover:bg-[var(--table-row-hover)]"
    >
      <td className="px-4 py-3 align-top font-medium text-[var(--seo-subheading)]">
        <span className="break-all">{r.url}</span>
        {r.status_code && r.status_code !== 200 ? (
          <span className="ml-2 text-xs text-[var(--seo-error)]">{r.status_code}</span>
        ) : null}
      </td>
      <td className="px-4 py-3 align-top">
        <TypeBadge url={r.url} auditType={r.audit_type} />
      </td>
      <td className="px-4 py-3 align-top">
        <ScoreBadge score={r.seo_score ?? 0} />
      </td>
      <td className="px-4 py-3 align-top">
        <EffortChips result={r} />
      </td>
      {/* Merged "Checklist + top issue" cell: the pass/warn/fail summary and the
          single highest-impact issue in one column (was two separate columns). */}
      <td className="px-4 py-3 align-top">
        {cl ? (
          <span className="flex flex-wrap items-center gap-1.5 text-xs">
            <StatusPill status="pass" /> {cl.pass}
            <StatusPill status="warning" /> {cl.warning}
            <StatusPill status="fail" /> {cl.fail}
          </span>
        ) : (
          <span className="text-xs text-[var(--seo-muted)]">Checklist N/A</span>
        )}
        <div className="mt-1 text-[var(--seo-text-light)]">
          {top || <span className="text-[var(--seo-success)]">No issues found</span>}
        </div>
      </td>
      <td className="px-4 py-3 text-right align-top">
        <span className="whitespace-nowrap text-sm font-medium text-[var(--seo-accent)]">View →</span>
      </td>
    </tr>
  );
}

function worstIssue(r: AuditResult): string {
  const issues = r.all_issues || [];
  if (issues.length === 0) return "";
  return [...issues].sort((a, b) => (b.impact_score ?? 0) - (a.impact_score ?? 0))[0].issue;
}

type SortMode = "score-asc" | "score-desc" | "issues-desc" | "alpha";

const SORT_OPTIONS: { value: SortMode; label: string }[] = [
  { value: "score-asc", label: "Worst score first" },
  { value: "score-desc", label: "Best score first" },
  { value: "issues-desc", label: "Most issues first" },
  { value: "alpha", label: "URL (A–Z)" },
];

function sortRows(rows: AuditResult[], mode: SortMode): AuditResult[] {
  const withKey = [...rows];
  switch (mode) {
    case "score-desc":
      return withKey.sort((a, b) => (b.seo_score ?? 0) - (a.seo_score ?? 0));
    case "issues-desc":
      return withKey.sort((a, b) => (b.all_issues?.length ?? 0) - (a.all_issues?.length ?? 0));
    case "alpha":
      return withKey.sort((a, b) => a.url.localeCompare(b.url));
    case "score-asc":
    default:
      return withKey.sort((a, b) => (a.seo_score ?? 0) - (b.seo_score ?? 0));
  }
}

export default function ResultsPage() {
  const { results, navFilter, setNavFilter, setSelectedUrlIndex, clearAll } = useAudit();
  const router = useRouter();

  const [scoreMax, setScoreMax] = useState(100);
  const [brokenOnly, setBrokenOnly] = useState(false);
  const [search, setSearch] = useState("");
  const [sortMode, setSortMode] = useState<SortMode>("alpha");
  const [confirmClear, setConfirmClear] = useState(false);
  const [h1ReportOpen, setH1ReportOpen] = useState(false);
  const [typeFilter, setTypeFilter] = useState("all");
  const [checklistFilter, setChecklistFilter] = useState<"all" | "has-fail" | "has-warning">("all");

  useEffect(() => {
    if (!navFilter) return;
    if (navFilter.kind === "score" && navFilter.key === "critical_urls") {
      setScoreMax(49);
    }
    setNavFilter(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [navFilter]);

  // Every distinct page-category (Type) across all results, for the Type filter.
  const types = useMemo(() => {
    return [...new Set(results.map((r) => categorizeUrl(r.url, r.audit_type)))].sort();
  }, [results]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return results.filter((r) => {
      if ((r.seo_score ?? 0) > scoreMax) return false;
      if (brokenOnly) {
        const brokenInt = r.internal_links?.broken_count || 0;
        const brokenExt = r.external_links?.broken_count || 0;
        if (brokenInt + brokenExt === 0) return false;
      }
      if (q && !r.url.toLowerCase().includes(q)) return false;
      if (typeFilter !== "all" && categorizeUrl(r.url, r.audit_type) !== typeFilter) return false;
      const cl = r.technical_audit_checklist?.summary;
      if (checklistFilter === "has-fail" && !(cl && cl.fail > 0)) return false;
      if (checklistFilter === "has-warning" && !(cl && cl.warning > 0)) return false;
      return true;
    });
  }, [results, scoreMax, brokenOnly, search, typeFilter, checklistFilter]);

  // Flat, sorted rows. The old domain/section hierarchy (a collapsible
  // "example.com › /section" tree) was removed in favor of a single flat table
  // with a Type column, per the requested design.
  const sortedRows = useMemo(() => sortRows(filtered, sortMode), [filtered, sortMode]);

  function openDetail(r: AuditResult) {
    setSelectedUrlIndex(results.indexOf(r));
    router.push("/detail");
  }

  function openDetailByUrl(url: string) {
    const idx = results.findIndex((r) => r.url === url);
    if (idx < 0) return;
    setSelectedUrlIndex(idx);
    router.push("/detail");
  }

  function exportSiteH1Csv() {
    const rows = [["URL", "H1 Text", "H1 Count"]];
    for (const r of results) {
      rows.push([r.url, r.heading_detail?.h1_text || "", String(r.heading_detail?.counts?.h1 ?? 0)]);
    }
    downloadCsv("site-h1-report.csv", rows);
  }

  // Sitewide rollup: only meaningful when more than one URL was audited.
  const rollup = useMemo(() => {
    if (results.length < 2) return null;
    const issues = allIssuesOf(results);
    const dist = { good: 0, warn: 0, poor: 0 };
    for (const r of results) {
      const s = r.seo_score ?? 0;
      if (s >= 70) dist.good++;
      else if (s >= 50) dist.warn++;
      else dist.poor++;
    }
    // Top failing checks: grouped by issue title, severity-first, each carrying
    // the exact affected-page URLs so the user can drill straight into them.
    const topFailing = issuesByTitle(results).slice(0, 8);
    return {
      avg: avgScore(results),
      totalIssues: issues.length,
      dist,
      topFailing,
    };
  }, [results]);

  if (results.length === 0) {
    return (
      <div>
        <PageHeader icon={<ListChecksIcon size={18} />} title="Audit Results" />
        <EmptyState title="No audits yet" hint="Run an audit to see results here." />
      </div>
    );
  }

  return (
    <div>
      <PageHeader icon={<ListChecksIcon size={18} />} title="Audit Results" subtitle={`${filtered.length} of ${results.length} URLs`} />

      {rollup ? (
        <Card className="mb-4">
          <h3 className="mb-3 text-sm font-semibold text-[var(--seo-subheading)]">
            Sitewide Summary
          </h3>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-[auto_1fr]">
            <div className="flex items-center gap-6">
              <ScoreCircle score={rollup.avg} size={88} label="Avg score" />
              <div className="flex flex-col gap-1 text-sm">
                <span className="text-[var(--seo-text-light)]">
                  <strong className="text-[var(--seo-heading)]">{results.length}</strong> URLs audited
                </span>
                <span className="text-[var(--seo-text-light)]">
                  <strong className="text-[var(--seo-heading)]">{rollup.totalIssues}</strong> total issues
                </span>
                <span className="flex gap-3 text-xs">
                  <span style={{ color: "var(--seo-success)" }}>● {rollup.dist.good} good</span>
                  <span style={{ color: "var(--seo-warning)" }}>● {rollup.dist.warn} fair</span>
                  <span style={{ color: "var(--seo-error)" }}>● {rollup.dist.poor} poor</span>
                </span>
              </div>
            </div>
            <div>
              <div className="mb-2 text-xs font-medium uppercase tracking-wide text-[var(--seo-muted)]">
                Top failing checks (site-wide)
              </div>
              <div className="flex flex-col gap-1.5">
                {rollup.topFailing.map((f) => (
                  <FailingIssueRow key={f.issue} issue={f} onOpenUrl={openDetailByUrl} />
                ))}
              </div>
            </div>
          </div>
        </Card>
      ) : null}

      {rollup ? (
        <AiSummaryCard
          className="mb-4"
          cacheKey="__sitewide__"
          seoScore={Math.round(rollup.avg)}
          issues={allIssuesOf(results)}
          contextLabel={`across ${results.length} audited pages (sitewide)`}
        />
      ) : null}

      {/* Sitewide (cross-URL) concept, moved here from the per-URL Detail
          page's Headings tab where it was organizationally out of place -
          a report iterating every audited URL doesn't belong on a single
          URL's drill-down page. */}
      {results.length > 1 ? (
        <Card className="mb-4 overflow-hidden p-0">
          <button
            type="button"
            onClick={() => setH1ReportOpen((v) => !v)}
            className="flex w-full items-center justify-between gap-3 px-5 py-3 text-left"
          >
            <span className="flex items-center gap-2">
              <span className={`text-[var(--seo-muted)] transition-transform ${h1ReportOpen ? "rotate-90" : ""}`}>▸</span>
              <span className="text-sm font-semibold text-[var(--seo-subheading)]">Sitewide H1 Report</span>
            </span>
          </button>
          {h1ReportOpen ? (
            <div className="overflow-x-auto border-t border-[var(--seo-border)]">
              <div className="flex justify-end p-3">
                <button
                  onClick={exportSiteH1Csv}
                  className="rounded-lg border border-[var(--seo-border-strong)] px-3 py-1.5 text-xs font-medium hover:bg-[var(--seo-card-hover)]"
                >
                  Export CSV
                </button>
              </div>
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-[var(--seo-border)] bg-[var(--table-header-bg)] text-left text-xs uppercase tracking-wide text-[var(--seo-muted)]">
                    <th className="px-4 py-3">URL</th>
                    <th className="px-4 py-3">H1 Text</th>
                    <th className="px-4 py-3">H1 Count</th>
                  </tr>
                </thead>
                <tbody>
                  {results.map((res) => (
                    <tr key={res.url} className="border-b border-[var(--table-row-border)]">
                      <td className="max-w-xs truncate px-4 py-3">{res.url}</td>
                      <td className="px-4 py-3">{res.heading_detail?.h1_text || <em>none</em>}</td>
                      <td className="px-4 py-3">{res.heading_detail?.counts?.h1 ?? 0}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </Card>
      ) : null}

      <Card className="mb-4">
        <div className="flex flex-wrap items-end gap-6">
          <div className="min-w-[200px] flex-1">
            <label className="mb-1 block text-xs font-medium text-[var(--seo-muted)]">
              Search URL
            </label>
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Filter by path or domain…"
              className="w-full rounded-lg border border-[var(--seo-border)] bg-[var(--seo-card-bg)] px-3 py-1.5 text-sm text-[var(--seo-text)] placeholder:text-[var(--seo-muted)]"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-[var(--seo-muted)]">
              Max score: {scoreMax}
            </label>
            <input
              type="range"
              min={0}
              max={100}
              value={scoreMax}
              onChange={(e) => setScoreMax(Number(e.target.value))}
              className="w-48"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-[var(--seo-muted)]">
              Sort
            </label>
            <select
              value={sortMode}
              onChange={(e) => setSortMode(e.target.value as SortMode)}
              className="rounded-lg border border-[var(--seo-border)] bg-[var(--seo-card-bg)] px-3 py-1.5 text-sm text-[var(--seo-text)]"
            >
              {SORT_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          </div>
          {types.length > 1 ? (
            <div>
              <label className="mb-1 block text-xs font-medium text-[var(--seo-muted)]">
                Type
              </label>
              <select
                value={typeFilter}
                onChange={(e) => setTypeFilter(e.target.value)}
                className="rounded-lg border border-[var(--seo-border)] bg-[var(--seo-card-bg)] px-3 py-1.5 text-sm text-[var(--seo-text)]"
              >
                <option value="all">All types</option>
                {types.map((t) => (
                  <option key={t} value={t}>{t}</option>
                ))}
              </select>
            </div>
          ) : null}
          <div>
            <label className="mb-1 block text-xs font-medium text-[var(--seo-muted)]">
              Checklist
            </label>
            <select
              value={checklistFilter}
              onChange={(e) => setChecklistFilter(e.target.value as typeof checklistFilter)}
              className="rounded-lg border border-[var(--seo-border)] bg-[var(--seo-card-bg)] px-3 py-1.5 text-sm text-[var(--seo-text)]"
            >
              <option value="all">All checklist results</option>
              <option value="has-fail">Has failures</option>
              <option value="has-warning">Has warnings</option>
            </select>
          </div>
          <label className="flex items-center gap-2 text-sm text-[var(--seo-text)]">
            <input
              type="checkbox"
              checked={brokenOnly}
              onChange={(e) => setBrokenOnly(e.target.checked)}
            />
            Broken links only
          </label>
        </div>
      </Card>

      <ExportBar results={filtered} totalCount={results.length} />

      <div className="mb-4">
        <Card className="overflow-hidden p-0">
          {sortedRows.length === 0 ? (
            <div className="px-4 py-10 text-center text-sm text-[var(--seo-muted)]">
              No URLs match the current filters.
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-[var(--seo-border)] bg-[var(--table-header-bg)] text-left text-xs uppercase tracking-wide text-[var(--seo-muted)]">
                    <th className="px-4 py-2.5">URL</th>
                    <th className="px-4 py-2.5">Type</th>
                    <th className="px-4 py-2.5">Score</th>
                    <th className="px-4 py-2.5">Fix effort</th>
                    <th className="px-4 py-2.5">Checklist &amp; top issue</th>
                    <th className="px-4 py-2.5" />
                  </tr>
                </thead>
                <tbody>
                  {sortedRows.map((r, idx) => (
                    <ResultRow key={r.url + idx} r={r} onOpen={openDetail} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>

      {/* Destructive action, deliberately separated from the filter/export
          controls above so it isn't a stray click away from routine actions. */}
      <div className="mt-6 flex justify-end border-t border-[var(--seo-border)] pt-4">
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
          onBlur={() => setConfirmClear(false)}
          className="rounded-lg border border-[var(--seo-error-border)] px-3 py-1.5 text-sm font-medium text-[var(--seo-error)] hover:bg-[var(--seo-error-bg)]"
        >
          {confirmClear ? "Confirm clear all results?" : "Clear All Results"}
        </button>
      </div>
    </div>
  );
}
