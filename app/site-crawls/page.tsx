"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Card, EmptyState, PageHeader, ScoreCircle } from "@/components/ui";
import { GlobeIcon, PlusIcon } from "@/components/icons";
import { formatDate } from "@/lib/format";
import { SCHEDULE_PRESETS } from "@/lib/schedulePresets";
import { runPersistedCrawl } from "@/lib/crawl/persistedCrawl";

interface CrawlSummary {
  id: number;
  rootUrl: string | null;
  status: string;
  healthScore: number | null;
  seoScoreAvg: number | null;
  pagesCrawled: number;
  pagesTotalEstimate: number | null;
  startedAt: string | null;
  finishedAt: string | null;
}

const STATUS_STYLE: Record<string, { color: string; bg: string; label: string }> = {
  queued: { color: "var(--seo-muted)", bg: "var(--seo-card-hover)", label: "Queued" },
  running: { color: "var(--seo-accent)", bg: "var(--seo-accent-light)", label: "Running" },
  completed: { color: "var(--seo-success)", bg: "var(--seo-success-bg)", label: "Completed" },
  failed: { color: "var(--seo-error)", bg: "var(--seo-error-bg)", label: "Failed" },
};

function CrawlStatusPill({ status }: { status: string }) {
  const s = STATUS_STYLE[status] ?? STATUS_STYLE.queued;
  return (
    <span className="pill" style={{ color: s.color, backgroundColor: s.bg }}>
      {s.label}
    </span>
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

export default function SiteCrawlsPage() {
  const router = useRouter();
  const [crawls, setCrawls] = useState<CrawlSummary[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [rootUrl, setRootUrl] = useState("");
  const [maxPages, setMaxPages] = useState(50);
  const [maxDepth, setMaxDepth] = useState(3);
  const [robotsMode, setRobotsMode] = useState("respect");
  const [renderJs, setRenderJs] = useState(false);
  const [schedulePreset, setSchedulePreset] = useState("off");
  const [customCron, setCustomCron] = useState("");
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  const [progress, setProgress] = useState<string | null>(null);

  async function loadCrawls() {
    try {
      const data = await postCrawlsAction<{ crawls: CrawlSummary[] }>({ action: "list" });
      setCrawls(data.crawls);
      setLoadError(null);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "Failed to load crawls.");
    }
  }

  useEffect(() => {
    loadCrawls();
  }, []);

  async function startCrawl() {
    if (!rootUrl.trim()) return;
    setStarting(true);
    setStartError(null);
    setProgress("Starting crawl…");
    try {
      const scheduleCron =
        schedulePreset === "off"
          ? null
          : schedulePreset === "custom"
            ? customCron.trim() || null
            : (SCHEDULE_PRESETS.find((p) => p.id === schedulePreset)?.cron ?? null);
      const data = await postCrawlsAction<{ crawlId: number }>({
        action: "create",
        rootUrl: rootUrl.trim(),
        maxPages,
        maxDepth,
        robotsMode,
        renderJs,
        scheduleCron,
      });
      // Browser drives the crawl and streams each audited page into the DB
      // (Vercel-only - no worker). Progress shows here until it finalizes.
      setProgress("Discovering pages…");
      await runPersistedCrawl({
        crawlId: data.crawlId,
        rootUrl: rootUrl.trim(),
        maxPages,
        maxDepth,
        robotsMode,
        onProgress: (p) => setProgress(`Auditing pages… ${p.done}/${p.discovered}`),
      });
      router.push(`/site-crawls/${data.crawlId}`);
    } catch (err) {
      setStartError(err instanceof Error ? err.message : "Failed to run crawl.");
      setStarting(false);
      setProgress(null);
    }
  }

  return (
    <div>
      <PageHeader
        icon={<GlobeIcon size={18} />}
        title="Site Crawls"
        subtitle="Crawl an entire site - page discovery, per-page audits, and site-wide checks (duplicates, orphan pages, broken links)."
      />

      <Card className="mb-6">
        <h2 className="mb-3 text-sm font-semibold text-[var(--seo-heading)]">Start a new crawl</h2>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-[2fr_1fr_1fr_1fr_auto]">
          <input
            type="url"
            placeholder="https://example.com"
            value={rootUrl}
            onChange={(e) => setRootUrl(e.target.value)}
            className="rounded-lg border border-[var(--seo-border)] bg-[var(--seo-card-bg)] px-3 py-2 text-sm text-[var(--seo-heading)] outline-none focus:border-[var(--seo-accent)]"
          />
          <input
            type="number"
            min={1}
            value={maxPages}
            onChange={(e) => setMaxPages(Number(e.target.value) || 1)}
            title="Max pages"
            className="rounded-lg border border-[var(--seo-border)] bg-[var(--seo-card-bg)] px-3 py-2 text-sm text-[var(--seo-heading)] outline-none focus:border-[var(--seo-accent)]"
          />
          <input
            type="number"
            min={0}
            value={maxDepth}
            onChange={(e) => setMaxDepth(Number(e.target.value) || 0)}
            title="Max depth"
            className="rounded-lg border border-[var(--seo-border)] bg-[var(--seo-card-bg)] px-3 py-2 text-sm text-[var(--seo-heading)] outline-none focus:border-[var(--seo-accent)]"
          />
          <select
            value={robotsMode}
            onChange={(e) => setRobotsMode(e.target.value)}
            className="rounded-lg border border-[var(--seo-border)] bg-[var(--seo-card-bg)] px-3 py-2 text-sm text-[var(--seo-heading)] outline-none focus:border-[var(--seo-accent)]"
          >
            <option value="respect">Respect robots.txt</option>
            <option value="ignore">Ignore robots.txt</option>
            <option value="ignore_but_report">Ignore, but report</option>
          </select>
          <button
            type="button"
            onClick={startCrawl}
            disabled={starting || !rootUrl.trim()}
            className="inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-lg btn-gradient px-4 py-2 text-sm font-semibold text-white disabled:opacity-60"
          >
            <PlusIcon size={15} />
            {starting ? "Crawling…" : "Start Crawl"}
          </button>
        </div>
        <label className="mt-3 flex items-center gap-2 text-sm text-[var(--seo-text)]">
          <input
            type="checkbox"
            checked={renderJs}
            onChange={(e) => setRenderJs(e.target.checked)}
            className="h-4 w-4 accent-[var(--seo-accent)]"
          />
          Render JavaScript (slower - for SPAs/client-rendered sites)
        </label>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <label className="text-sm text-[var(--seo-text)]">Repeat this crawl:</label>
          <select
            value={schedulePreset}
            onChange={(e) => setSchedulePreset(e.target.value)}
            className="rounded-lg border border-[var(--seo-border)] bg-[var(--seo-card-bg)] px-3 py-1.5 text-sm text-[var(--seo-heading)] outline-none focus:border-[var(--seo-accent)]"
          >
            {SCHEDULE_PRESETS.map((preset) => (
              <option key={preset.id} value={preset.id}>
                {preset.label}
              </option>
            ))}
          </select>
          {schedulePreset === "custom" ? (
            <input
              type="text"
              placeholder="0 0 * * *"
              value={customCron}
              onChange={(e) => setCustomCron(e.target.value)}
              className="rounded-lg border border-[var(--seo-border)] bg-[var(--seo-card-bg)] px-3 py-1.5 font-mono text-sm text-[var(--seo-heading)] outline-none focus:border-[var(--seo-accent)]"
            />
          ) : null}
        </div>
        <p className="mt-2 text-xs text-[var(--seo-muted)]">
          Max pages / depth (0 = homepage only). The crawl runs in your browser while this page is
          open - keep the tab open until it finishes. Rendering uses a real browser per page and is
          much slower than a raw fetch.
        </p>
        {progress ? (
          <p className="mt-2 flex items-center gap-2 text-xs text-[var(--seo-accent)]">
            <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-[var(--seo-accent-border)] border-t-[var(--seo-accent)]" />
            {progress}
          </p>
        ) : null}
        {startError ? <p className="mt-2 text-xs text-[var(--seo-error)]">{startError}</p> : null}
      </Card>

      {loadError ? (
        <p className="mb-4 text-sm text-[var(--seo-error)]">{loadError}</p>
      ) : null}

      {crawls === null ? null : crawls.length === 0 ? (
        <EmptyState title="No crawls yet" hint="Start one above to see site-wide results here." />
      ) : (
        <div className="flex flex-col gap-3">
          {crawls.map((crawl) => (
            <Card
              key={crawl.id}
              className="cursor-pointer transition-colors hover:border-[var(--seo-border-strong)]"
            >
              <button
                type="button"
                onClick={() => router.push(`/site-crawls/${crawl.id}`)}
                className="flex w-full items-center gap-4 text-left"
              >
                {crawl.healthScore != null ? (
                  <ScoreCircle score={crawl.healthScore} size={48} />
                ) : (
                  <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-full border border-[var(--seo-border)] text-xs text-[var(--seo-muted)]">
                    -
                  </div>
                )}
                <div className="min-w-0 flex-1">
                  <div className="truncate font-mono text-sm text-[var(--seo-heading)]">
                    {crawl.rootUrl ?? `Crawl #${crawl.id}`}
                  </div>
                  <div className="mt-0.5 text-xs text-[var(--seo-muted)]">
                    {crawl.pagesCrawled} page{crawl.pagesCrawled === 1 ? "" : "s"} crawled
                    {crawl.finishedAt ? ` · finished ${formatDate(crawl.finishedAt)}` : ""}
                  </div>
                </div>
                <CrawlStatusPill status={crawl.status} />
              </button>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
