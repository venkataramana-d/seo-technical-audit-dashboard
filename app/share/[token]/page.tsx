"use client";

// Public, read-only shared crawl report (M5 T5.2). Rendered without the app
// chrome or auth gate (see components/AppShell.tsx isPublicRoute). The token in
// the route IS the capability: everything here talks to the public /api/share
// endpoint, which resolves the crawl by that token alone - no login, no crawlId.

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { Card, ScoreCircle } from "@/components/ui";
import { GlobeIcon } from "@/components/icons";
import { formatDate } from "@/lib/format";

interface Meta {
  id: number;
  rootUrl: string | null;
  status: string;
  healthScore: number | null;
  seoScoreAvg: number | null;
  pagesCrawled: number;
  startedAt: string | null;
  finishedAt: string | null;
}

interface Summary {
  meta: Meta;
  pagesCount: number;
  issueSeverityCounts: Record<string, number>;
  issuesCount: number;
}

interface PageRow {
  id: number;
  url: string;
  statusCode: number | null;
  title: string | null;
  seoScore: number | null;
  depth: number | null;
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
}

interface IssuesResponse {
  issues: IssueRow[];
  total: number;
  page: number;
  pageSize: number;
}

const PAGE_SIZE = 25;

const SEVERITY_STYLE: Record<string, { color: string; bg: string }> = {
  error: { color: "var(--seo-error)", bg: "var(--seo-error-bg)" },
  warning: { color: "var(--seo-warning)", bg: "var(--seo-warning-bg)" },
  notice: { color: "var(--seo-accent)", bg: "var(--seo-accent-light)" },
};

const GRID_TH =
  "border border-[var(--table-row-border)] bg-[var(--table-header-bg)] px-2 py-1.5 text-left text-[11px] font-semibold uppercase tracking-wide text-[var(--seo-muted)]";
const GRID_TD = "border border-[var(--table-row-border)] px-2 py-1 align-top text-xs text-[var(--seo-text)]";

function StatusDot({ color }: { color: string }) {
  return <span className="inline-block h-1.5 w-1.5 shrink-0 rounded-full" style={{ backgroundColor: color }} />;
}

/** POST to the public share endpoint. Never sends any auth - the token is it. */
async function postShare<T>(body: Record<string, unknown>): Promise<{ ok: boolean; status: number; data: T }> {
  const res = await fetch("/api/share", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = (await res.json().catch(() => ({}))) as T;
  return { ok: res.ok, status: res.status, data };
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

function PagesCard({ token }: { token: string }) {
  const [data, setData] = useState<PagesResponse | null>(null);
  const [page, setPage] = useState(1);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    postShare<PagesResponse>({ token, action: "pages", page, pageSize: PAGE_SIZE })
      .then(({ ok, data }) => {
        if (cancelled) return;
        if (!ok) {
          setError("Could not load pages.");
          return;
        }
        setData(data);
        setError(null);
      })
      .catch(() => {
        if (!cancelled) setError("Could not load pages.");
      });
    return () => {
      cancelled = true;
    };
  }, [token, page]);

  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.pageSize)) : 1;

  return (
    <Card>
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-[var(--seo-heading)]">Pages</h2>
        {data ? <span className="text-xs text-[var(--seo-muted)]">{data.total.toLocaleString()} total</span> : null}
      </div>
      {error ? <p className="text-xs text-[var(--seo-error)]">{error}</p> : null}
      {!data && !error ? <p className="text-xs text-[var(--seo-muted)]">Loading…</p> : null}
      {data && data.pages.length === 0 ? <p className="text-xs text-[var(--seo-muted)]">No pages.</p> : null}
      {data && data.pages.length > 0 ? (
        <div className="max-h-[420px] overflow-auto">
          <table className="w-full border-collapse text-left text-sm">
            <thead>
              <tr className="sticky top-0 z-10">
                <th className={GRID_TH}>URL</th>
                <th className={GRID_TH}>Status</th>
                <th className={GRID_TH}>Title</th>
                <th className={GRID_TH}>Score</th>
                <th className={GRID_TH}>Depth</th>
                <th className={GRID_TH}>Issues</th>
              </tr>
            </thead>
            <tbody>
              {data.pages.map((p) => {
                const statusColor =
                  p.statusCode == null ? "var(--seo-muted)" : p.statusCode >= 400 ? "var(--seo-error)" : "var(--seo-success)";
                return (
                  <tr key={p.id}>
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
                    <td className={`${GRID_TD} tabular-nums`}>{p.depth ?? "-"}</td>
                    <td className={GRID_TD}>
                      <div className="flex flex-wrap items-center gap-2">
                        {Object.entries(p.issueCounts).length === 0 ? (
                          <span className="text-[var(--seo-success)]">0</span>
                        ) : (
                          Object.entries(p.issueCounts).map(([sev, count]) => (
                            <span
                              key={sev}
                              className="inline-flex items-center gap-1 tabular-nums"
                              style={{ color: (SEVERITY_STYLE[sev] ?? SEVERITY_STYLE.notice).color }}
                            >
                              <StatusDot color={(SEVERITY_STYLE[sev] ?? SEVERITY_STYLE.notice).color} />
                              {count}
                            </span>
                          ))
                        )}
                      </div>
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

function IssuesCard({ token }: { token: string }) {
  const [data, setData] = useState<IssuesResponse | null>(null);
  const [severity, setSeverity] = useState("");
  const [page, setPage] = useState(1);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setPage(1);
  }, [severity]);

  useEffect(() => {
    let cancelled = false;
    postShare<IssuesResponse>({ token, action: "issues", severity: severity || undefined, page, pageSize: PAGE_SIZE })
      .then(({ ok, data }) => {
        if (cancelled) return;
        if (!ok) {
          setError("Could not load issues.");
          return;
        }
        setData(data);
        setError(null);
      })
      .catch(() => {
        if (!cancelled) setError("Could not load issues.");
      });
    return () => {
      cancelled = true;
    };
  }, [token, severity, page]);

  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.pageSize)) : 1;

  return (
    <Card>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-[var(--seo-heading)]">Issues</h2>
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
      </div>
      {error ? <p className="text-xs text-[var(--seo-error)]">{error}</p> : null}
      {!data && !error ? <p className="text-xs text-[var(--seo-muted)]">Loading…</p> : null}
      {data && data.issues.length === 0 ? <p className="text-xs text-[var(--seo-muted)]">No issues found.</p> : null}
      {data && data.issues.length > 0 ? (
        <div className="max-h-[420px] overflow-auto">
          <table className="w-full border-collapse text-left text-sm">
            <thead>
              <tr className="sticky top-0 z-10">
                <th className={GRID_TH}>Severity</th>
                <th className={GRID_TH}>Issue</th>
                <th className={GRID_TH}>Category</th>
                <th className={GRID_TH}>Recommendation</th>
                <th className={GRID_TH}>Page</th>
              </tr>
            </thead>
            <tbody>
              {data.issues.map((issue) => {
                const color = (SEVERITY_STYLE[issue.severity] ?? SEVERITY_STYLE.notice).color;
                return (
                  <tr key={issue.id}>
                    <td className={GRID_TD}>
                      <span className="inline-flex items-center gap-1.5 capitalize" style={{ color }}>
                        <StatusDot color={color} />
                        {issue.severity}
                      </span>
                    </td>
                    <td className={`${GRID_TD} max-w-[200px] truncate text-[var(--seo-heading)]`} title={issue.issueType}>
                      {issue.issueType}
                    </td>
                    <td className={GRID_TD}>{issue.category}</td>
                    <td className={`${GRID_TD} max-w-[260px] truncate`} title={issue.recommendation}>
                      {issue.recommendation || "-"}
                    </td>
                    <td
                      className={`${GRID_TD} max-w-[200px] truncate font-mono text-[var(--seo-muted)]`}
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

function DownloadButtons({ token }: { token: string }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const download = useCallback(
    async (format: "csv" | "xlsx" | "pdf") => {
      setBusy(format);
      setError(null);
      try {
        const res = await fetch("/api/share", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ token, action: "export", format }),
        });
        if (!res.ok) {
          const data = await res.json().catch(() => ({}));
          throw new Error((data as { error?: string }).error || "Download failed.");
        }
        const blob = await res.blob();
        const disposition = res.headers.get("Content-Disposition") || "";
        const match = disposition.match(/filename="?([^"]+)"?/);
        const filename = match ? match[1] : `crawl-report.${format}`;
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = filename;
        a.click();
        URL.revokeObjectURL(url);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Download failed.");
      } finally {
        setBusy(null);
      }
    },
    [token],
  );

  const LABELS: Record<string, string> = { csv: "CSV", xlsx: "Excel", pdf: "PDF" };

  return (
    <div className="flex flex-wrap items-center gap-2">
      {(["csv", "xlsx", "pdf"] as const).map((fmt) => (
        <button
          key={fmt}
          type="button"
          disabled={busy != null}
          onClick={() => download(fmt)}
          className="rounded-lg border border-[var(--seo-border)] px-3 py-1.5 text-xs font-medium text-[var(--seo-text)] hover:bg-[var(--seo-card-hover)] disabled:opacity-60"
        >
          {busy === fmt ? "Preparing…" : `Download ${LABELS[fmt]}`}
        </button>
      ))}
      {error ? <span className="text-xs text-[var(--seo-error)]">{error}</span> : null}
    </div>
  );
}

export default function SharedReportPage() {
  const params = useParams<{ token: string }>();
  const token = Array.isArray(params.token) ? params.token[0] : params.token;

  const [summary, setSummary] = useState<Summary | null>(null);
  const [invalid, setInvalid] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    postShare<Summary & { error?: string }>({ token, action: "summary" })
      .then(({ ok, status, data }) => {
        if (cancelled) return;
        if (status === 404) {
          setInvalid(true);
          return;
        }
        if (!ok) {
          setError((data as { error?: string }).error || "Could not load this report.");
          return;
        }
        setSummary(data);
      })
      .catch(() => {
        if (!cancelled) setError("Could not load this report.");
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  if (invalid) {
    return (
      <main className="mx-auto flex min-h-screen max-w-lg flex-col items-center justify-center gap-3 px-4 text-center">
        <div className="flex h-12 w-12 items-center justify-center rounded-full bg-[var(--seo-error-bg)] text-[var(--seo-error)]">
          <GlobeIcon size={22} />
        </div>
        <h1 className="text-lg font-semibold text-[var(--seo-heading)]">Link unavailable</h1>
        <p className="text-sm text-[var(--seo-muted)]">
          This shared report link is invalid or has been revoked. Ask the person who shared it for a new link.
        </p>
      </main>
    );
  }

  if (error) {
    return (
      <main className="mx-auto flex min-h-screen max-w-lg flex-col items-center justify-center gap-3 px-4 text-center">
        <h1 className="text-lg font-semibold text-[var(--seo-heading)]">Something went wrong</h1>
        <p className="text-sm text-[var(--seo-muted)]">{error}</p>
      </main>
    );
  }

  if (!summary) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[var(--seo-app-bg)]">
        <div
          className="h-6 w-6 animate-spin rounded-full border-2 border-[var(--seo-border-strong)] border-t-[var(--seo-accent)]"
          aria-label="Loading"
        />
      </div>
    );
  }

  const { meta } = summary;

  return (
    <main className="min-h-screen bg-[var(--seo-app-bg)]">
      <div className="mx-auto w-full max-w-[1200px] px-4 py-8 md:px-8">
        {/* Header */}
        <div className="mb-6 flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
          <div className="min-w-0">
            <div className="mb-1 flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wide text-[var(--seo-muted)]">
              <GlobeIcon size={14} />
              Shared SEO report
            </div>
            <h1 className="break-all text-xl font-semibold text-[var(--seo-heading)]">
              {meta.rootUrl || "Crawl report"}
            </h1>
            <p className="mt-1 text-xs text-[var(--seo-muted)]">
              {meta.pagesCrawled.toLocaleString()} pages crawled · {summary.issuesCount.toLocaleString()} issues ·
              Finished {formatDate(meta.finishedAt)}
            </p>
          </div>
          <DownloadButtons token={token} />
        </div>

        {/* Score cards */}
        <div className="mb-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Card className="flex items-center gap-4">
            <ScoreCircle score={meta.healthScore ?? 0} size={64} label="Health Score" />
            <div className="text-xs text-[var(--seo-muted)]">% of crawled pages with zero error-severity issues</div>
          </Card>
          <Card className="flex items-center gap-4">
            <ScoreCircle score={meta.seoScoreAvg ?? 0} size={64} label="SEO Score (avg)" />
            <div className="text-xs text-[var(--seo-muted)]">Weighted per-page score, averaged across the crawl</div>
          </Card>
        </div>

        {/* Severity summary */}
        {Object.keys(summary.issueSeverityCounts).length > 0 ? (
          <div className="mb-4 flex flex-wrap gap-2">
            {Object.entries(summary.issueSeverityCounts).map(([sev, count]) => {
              const s = SEVERITY_STYLE[sev] ?? SEVERITY_STYLE.notice;
              return (
                <span key={sev} className="pill capitalize" style={{ color: s.color, backgroundColor: s.bg }}>
                  {count} {sev}
                </span>
              );
            })}
          </div>
        ) : null}

        <div className="flex flex-col gap-4">
          <PagesCard token={token} />
          <IssuesCard token={token} />
        </div>

        <p className="mt-6 text-center text-xs text-[var(--seo-muted)]">Read-only report · shared via a private link</p>
      </div>
    </main>
  );
}
