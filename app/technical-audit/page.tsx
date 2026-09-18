"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useAudit } from "@/lib/state/AuditContext";
import { Card, HelpSection, PageHeader } from "@/components/ui";
import { FileTextIcon, LinkIcon, MapIcon, NetworkIcon, ScanIcon } from "@/components/icons";
import type { ComponentType, SVGProps } from "react";
import type { AuditResult } from "@/lib/types";
import { parseUrlList } from "@/lib/crawl/parseUrlList";
import { DEFAULT_CONCURRENCY, MAX_CONCURRENCY } from "@/lib/crawl/orchestrator";
import {
  CHUNK_SIZE,
  clearCheckpoint,
  loadCheckpoint,
  runChunked,
  type ChunkedJobCheckpoint,
  type ChunkedProgress,
} from "@/lib/crawl/chunkedRunner";
import { fetchDomainHealth } from "@/lib/crawl/siteHealthCache";
import { ChecklistExplainer } from "@/components/ChecklistExplainer";
import { CheckSelector } from "@/components/CheckSelector";

type InputMode = "single" | "sitemap" | "list" | "crawl";

const DEFAULT_LIMIT = 50;
// Matches the backend's bulk-audit cap (modules/_http.py::bulk_url_cap) —
// 5000 everywhere (production, preview, and local dev). Every bulk mode
// (sitemap, crawl, CSV/paste) shares this same cap; a bare number input used
// to be the only place this was surfaced, so a clear line above each URL
// input now states it too (see BulkLimitNote below).
const MAX_LIMIT = Number(process.env.NEXT_PUBLIC_BULK_URL_LIMIT) || 5000;
const CRAWL_MAX_LIMIT = MAX_LIMIT;

type ModeIcon = ComponentType<SVGProps<SVGSVGElement> & { size?: number }>;

const MODES: { id: InputMode; label: string; icon: ModeIcon; hint: string; help: string }[] = [
  {
    id: "single", label: "Single URL", icon: LinkIcon, hint: "Audit one page.",
    help: "Runs the full 35-check technical audit on exactly one URL you enter. Best when you just want to check one page: a new blog post, a landing page you're about to publish, or a page a client asked about.",
  },
  {
    id: "sitemap", label: "Sitemap", icon: MapIcon, hint: "Sitewide audit from sitemap.xml.",
    help: "Reads a site's sitemap.xml (following nested sitemap-index files automatically) and audits a sample of the URLs it finds. Best for a broad health check across an entire site without manually listing every page.",
  },
  {
    id: "crawl", label: "Crawl from URL", icon: NetworkIcon, hint: "Discover pages by following links, no sitemap needed.",
    help: "Starts at one URL and discovers more pages by following its internal links, the same way a search engine crawler would. Best when a site has no sitemap, or you want to check exactly what's actually reachable by clicking around the site.",
  },
  {
    id: "list", label: "CSV / Paste URLs", icon: FileTextIcon, hint: "Bulk audit a list of URLs.",
    help: "Audits a specific list of URLs you provide: paste them one per line, or upload a CSV/TXT file with a url/link column. Best when you already know exactly which pages you want checked (e.g. a client's priority page list).",
  },
];

export default function TechnicalAuditPage() {
  const router = useRouter();
  const { addResult, addResults } = useAudit();

  const [mode, setMode] = useState<InputMode>("single");

  // Shared audit options
  const [auditType, setAuditType] = useState<"auto" | "course" | "blog" | "general">("auto");
  const [checkLinks, setCheckLinks] = useState(true);
  const [fetchPagespeed, setFetchPagespeed] = useState(false);

  // Single-URL
  const [url, setUrl] = useState("");

  // Sitemap
  const [sitemapUrl, setSitemapUrl] = useState("");
  const [includePattern, setIncludePattern] = useState("");
  const [excludePattern, setExcludePattern] = useState("");

  // Crawl from URL
  const [crawlSeedUrl, setCrawlSeedUrl] = useState("");
  const [includeSubdomains, setIncludeSubdomains] = useState(false);
  const [robotsMode, setRobotsMode] = useState<"respect" | "ignore" | "ignore_but_report">("respect");
  const [maxDepth, setMaxDepth] = useState(3);

  // List / CSV
  const [pastedList, setPastedList] = useState("");

  // Bulk options
  const [limit, setLimit] = useState(DEFAULT_LIMIT);
  const [concurrency, setConcurrency] = useState(DEFAULT_CONCURRENCY);

  const [error, setError] = useState<string | null>(null);
  const [phase, setPhase] = useState<"idle" | "resolving" | "crawling" | "done">("idle");
  const [progress, setProgress] = useState<ChunkedProgress | null>(null);
  const [resolvedCount, setResolvedCount] = useState<{ found: number; capped: boolean } | null>(null);
  const [resumable, setResumable] = useState<ChunkedJobCheckpoint | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const running = phase === "resolving" || phase === "crawling";
  const effectiveMaxLimit = mode === "crawl" ? CRAWL_MAX_LIMIT : MAX_LIMIT;
  // Crawl mode's cap is lower than sitemap/list; derive the clamped value
  // instead of syncing `limit` state to it via an effect, so a higher limit
  // set before switching modes doesn't need a render-triggering side effect.
  const clampedLimit = Math.min(limit, effectiveMaxLimit);

  // Offer to resume an interrupted chunked run left over from a prior session.
  useEffect(() => {
    loadCheckpoint().then((cp) => {
      if (cp && cp.remaining.length > 0) setResumable(cp);
    });
  }, []);

  function handleFile(file: File) {
    const reader = new FileReader();
    reader.onload = () => setPastedList(String(reader.result || ""));
    reader.readAsText(file);
  }

  async function runFromCheckpoint(cp: ChunkedJobCheckpoint) {
    setResumable(null);
    setError(null);
    setResolvedCount(null);
    const controller = new AbortController();
    abortRef.current = controller;
    setPhase("crawling");
    const completedSoFar = cp.urls.length - cp.remaining.length;
    setProgress({
      total: cp.urls.length, completed: completedSoFar, succeeded: cp.succeeded, failed: cp.failed,
      inFlight: 0, lastUrl: "", currentChunk: Math.floor(completedSoFar / CHUNK_SIZE) + 1,
      totalChunks: Math.max(1, Math.ceil(cp.urls.length / CHUNK_SIZE)),
    });
    await runChunkedAndPersist(cp.urls, cp.remaining, cp.options, cp.label, controller, {
      succeeded: cp.succeeded, failed: cp.failed, startedAt: cp.startedAt,
    });
  }

  function discardCheckpoint() {
    clearCheckpoint();
    setResumable(null);
  }

  async function runChunkedAndPersist(
    allUrls: string[],
    remainingUrls: string[],
    opts: Parameters<typeof runChunked>[2],
    label: string,
    controller: AbortController,
    resumeFrom: Parameters<typeof runChunked>[5] = null,
  ) {
    const batch: AuditResult[] = [];
    await runChunked(
      allUrls, remainingUrls, opts, label,
      {
        signal: controller.signal,
        onProgress: (p) => setProgress(p),
        onResult: (r) => {
          batch.push(r);
          // Flush to persisted state every 5 results so /results updates live-ish.
          if (batch.length >= 5) {
            addResults(batch.splice(0, batch.length));
          }
        },
      },
      resumeFrom,
    );
    if (batch.length) addResults(batch);
    abortRef.current = null;
    setPhase(controller.signal.aborted ? "idle" : "done");
    if (controller.signal.aborted) {
      // Checkpoint was left in place by runChunked; let the user resume next time.
      loadCheckpoint().then((cp) => cp && setResumable(cp));
    }
  }

  async function runSingle() {
    if (!url.trim()) return;
    setPhase("crawling");
    setError(null);
    // Wire an AbortController so the "Cancel" button (which calls
    // abortRef.current?.abort()) actually cancels the in-flight single audit.
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const res = await fetch("/api/audit-pipeline", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "audit", url: url.trim(), auditType, checkLinks, fetchPagespeed }),
        signal: controller.signal,
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.error || "Audit failed.");
        setPhase("idle");
        return;
      }
      const result = data as AuditResult;
      if (result.fetch_error) setError(result.fetch_error);
      addResult(result);
      router.push("/detail");
    } catch (err) {
      // A user-initiated cancel surfaces as an AbortError — just return to idle
      // without showing it as a failure.
      if (controller.signal.aborted) {
        setPhase("idle");
        return;
      }
      setError(err instanceof Error ? err.message : "Audit failed.");
      setPhase("idle");
    } finally {
      abortRef.current = null;
    }
  }

  async function resolveUrls(): Promise<string[] | null> {
    if (mode === "list") {
      const parsed = parseUrlList(pastedList);
      if (parsed.urls.length === 0) {
        setError("No valid http(s) URLs found in the list.");
        return null;
      }
      const capped = parsed.urls.slice(0, clampedLimit);
      setResolvedCount({ found: parsed.urls.length, capped: parsed.urls.length > clampedLimit });
      return capped;
    }

    if (mode === "crawl") {
      if (!crawlSeedUrl.trim()) {
        setError("Enter a URL to start crawling from.");
        return null;
      }
      setPhase("resolving");
      const res = await fetch("/api/audit-pipeline", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action: "crawl",
          seedUrl: crawlSeedUrl.trim(),
          maxPages: clampedLimit,
          maxDepth,
          includeSubdomains,
          robotsMode,
          includePattern: includePattern.trim() || undefined,
          excludePattern: excludePattern.trim() || undefined,
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.error || "Crawl failed.");
        setPhase("idle");
        return null;
      }
      setResolvedCount({ found: data.total_found, capped: data.capped });
      return data.urls as string[];
    }

    // sitemap
    if (!sitemapUrl.trim()) {
      setError("Enter a sitemap URL or domain.");
      return null;
    }
    setPhase("resolving");
    const res = await fetch("/api/audit-pipeline", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        action: "sitemap",
        sitemapUrl: sitemapUrl.trim(),
        limit: clampedLimit,
        includePattern: includePattern.trim() || undefined,
        excludePattern: excludePattern.trim() || undefined,
      }),
    });
    const data = await res.json();
    if (!res.ok) {
      setError(data.error || "Could not read sitemap.");
      setPhase("idle");
      return null;
    }
    setResolvedCount({ found: data.total_found, capped: data.capped });
    return data.urls as string[];
  }

  function bulkLabel(): string {
    if (mode === "sitemap") return `Sitemap: ${sitemapUrl.trim()}`;
    if (mode === "crawl") return `Crawl: ${crawlSeedUrl.trim()}`;
    return "CSV / Pasted URL list";
  }

  async function runBulk() {
    setError(null);
    setProgress(null);
    setResolvedCount(null);
    const urls = await resolveUrls();
    if (!urls || urls.length === 0) {
      if (!error) setError("No URLs to audit.");
      setPhase("idle");
      return;
    }

    const controller = new AbortController();
    abortRef.current = controller;

    // Phase 2: fetch each domain's site-health once up front so the per-URL
    // audits skip re-running WHOIS/DNS/SSL/robots for every page on the domain.
    setPhase("resolving");
    const domainHealth = await fetchDomainHealth(urls, controller.signal);
    if (controller.signal.aborted) {
      abortRef.current = null;
      setPhase("idle");
      return;
    }

    setPhase("crawling");
    const totalChunks = Math.max(1, Math.ceil(urls.length / CHUNK_SIZE));
    setProgress({ total: urls.length, completed: 0, succeeded: 0, failed: 0, inFlight: 0, lastUrl: "", currentChunk: 1, totalChunks });

    await runChunkedAndPersist(
      urls, urls,
      { auditType, checkLinks, fetchPagespeed, concurrency, domainHealth },
      bulkLabel(), controller,
    );
  }

  function cancel() {
    // Aborts the in-flight chunk; runChunked leaves its checkpoint in place
    // (a resumable pause, not a full stop) for non-single-URL runs.
    abortRef.current?.abort();
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (mode === "single") return runSingle();
    return runBulk();
  }

  const inputClass =
    "w-full rounded-lg border border-[var(--seo-border-strong)] bg-[var(--seo-card-bg)] px-3 py-2 text-sm text-[var(--seo-text)] outline-none focus:border-[var(--seo-accent)]";

  return (
    <div>
      <PageHeader
        icon={<ScanIcon size={18} />}
        title="Technical Audit"
        subtitle="Run a technical SEO audit on a single URL, an entire sitemap, a crawl, or a list of URLs."
      />

      {resumable && !running ? (
        <Card className="mb-4 border-l-4 border-l-[var(--seo-warning)]">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="text-sm font-semibold text-[var(--seo-subheading)]">
                Interrupted audit found
              </div>
              <div className="text-xs text-[var(--seo-text-light)]">
                {resumable.label}: {resumable.urls.length - resumable.remaining.length} of{" "}
                {resumable.urls.length} done
              </div>
            </div>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => runFromCheckpoint(resumable)}
                className="rounded-lg btn-gradient px-3 py-1.5 text-sm font-semibold text-white"
              >
                Resume
              </button>
              <button
                type="button"
                onClick={discardCheckpoint}
                className="rounded-lg border border-[var(--seo-border-strong)] px-3 py-1.5 text-sm font-medium text-[var(--seo-text)] hover:bg-[var(--seo-card-hover)]"
              >
                Discard
              </button>
            </div>
          </div>
        </Card>
      ) : null}

      {/* Mode selector */}
      <div className="mb-2 grid grid-cols-2 gap-2 md:grid-cols-4">
        {MODES.map((m) => (
          <div
            key={m.id}
            role="button"
            tabIndex={running ? -1 : 0}
            aria-disabled={running}
            onClick={() => !running && setMode(m.id)}
            onKeyDown={(e) => {
              if (!running && (e.key === "Enter" || e.key === " ")) setMode(m.id);
            }}
            className={`relative flex flex-col items-start rounded-lg border p-3 text-left transition-colors ${
              running ? "cursor-default opacity-60" : "cursor-pointer"
            } ${
              mode === m.id
                ? "border-[var(--seo-accent)] bg-[var(--seo-accent-light)]"
                : "border-[var(--seo-border)] hover:border-[var(--seo-border-strong)] hover:bg-[var(--seo-card-hover)]"
            }`}
          >
            <span className={mode === m.id ? "text-[var(--seo-accent)]" : "text-[var(--seo-text-light)]"}>
              <m.icon size={20} />
            </span>
            <span className="mt-1.5 text-sm font-semibold text-[var(--seo-subheading)]">{m.label}</span>
            <span className="pr-4 text-xs text-[var(--seo-muted)]">{m.hint}</span>
          </div>
        ))}
      </div>

      <HelpSection title={MODES.find((m) => m.id === mode)?.label}>
        {MODES.find((m) => m.id === mode)?.help}
      </HelpSection>

      <div className="mt-4">
        <CheckSelector />
      </div>

      <Card>
        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          {mode === "single" ? (
            <Field label="URL to audit">
              <input
                type="url"
                required
                placeholder="https://example.com/page"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                className={inputClass}
              />
            </Field>
          ) : null}

          {mode === "sitemap" ? (
            <>
              <BulkLimitNote limit={MAX_LIMIT} />
              <Field label="Sitemap URL or domain">
                <input
                  type="text"
                  placeholder="https://www.example.com/sitemap.xml"
                  value={sitemapUrl}
                  onChange={(e) => setSitemapUrl(e.target.value)}
                  className={inputClass}
                />
              </Field>
              <div className="grid grid-cols-2 gap-3">
                <Field label="Include pattern (regex, optional)">
                  <input
                    type="text"
                    placeholder="/blog/"
                    value={includePattern}
                    onChange={(e) => setIncludePattern(e.target.value)}
                    className={inputClass}
                  />
                </Field>
                <Field label="Exclude pattern (regex, optional)">
                  <input
                    type="text"
                    placeholder="/tag/|/author/"
                    value={excludePattern}
                    onChange={(e) => setExcludePattern(e.target.value)}
                    className={inputClass}
                  />
                </Field>
              </div>
            </>
          ) : null}

          {mode === "crawl" ? (
            <>
              <BulkLimitNote limit={CRAWL_MAX_LIMIT} />
              <Field label="Start URL">
                <input
                  type="url"
                  placeholder="https://www.example.com/"
                  value={crawlSeedUrl}
                  onChange={(e) => setCrawlSeedUrl(e.target.value)}
                  className={inputClass}
                />
              </Field>
              <div className="grid grid-cols-2 gap-3">
                <Field label="Include pattern (regex, optional)">
                  <input
                    type="text"
                    placeholder="/blog/"
                    value={includePattern}
                    onChange={(e) => setIncludePattern(e.target.value)}
                    className={inputClass}
                  />
                </Field>
                <Field label="Exclude pattern (regex, optional)">
                  <input
                    type="text"
                    placeholder="/tag/|/author/"
                    value={excludePattern}
                    onChange={(e) => setExcludePattern(e.target.value)}
                    className={inputClass}
                  />
                </Field>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <Field label="Max crawl depth">
                  <input
                    type="number"
                    min={1}
                    max={10}
                    value={maxDepth}
                    onChange={(e) => setMaxDepth(Math.max(1, Math.min(10, Number(e.target.value) || 3)))}
                    className={inputClass}
                  />
                </Field>
                <Field label="robots.txt handling">
                  <select
                    value={robotsMode}
                    onChange={(e) => setRobotsMode(e.target.value as typeof robotsMode)}
                    className={inputClass}
                  >
                    <option value="respect">Respect (skip disallowed)</option>
                    <option value="ignore_but_report">Ignore but report</option>
                    <option value="ignore">Ignore</option>
                  </select>
                </Field>
              </div>
              <label className="flex items-center gap-2 text-sm text-[var(--seo-text)]">
                <input
                  type="checkbox"
                  checked={includeSubdomains}
                  onChange={(e) => setIncludeSubdomains(e.target.checked)}
                />
                Include subdomains
              </label>
            </>
          ) : null}

          {mode === "list" ? (
            <>
              <BulkLimitNote limit={MAX_LIMIT} />
              <Field label="Paste URLs (one per line) or a CSV with a url column">
                <textarea
                  rows={6}
                  placeholder={"https://example.com/\nhttps://example.com/about"}
                  value={pastedList}
                  onChange={(e) => setPastedList(e.target.value)}
                  className={`${inputClass} font-mono`}
                />
              </Field>
              <label className="text-sm text-[var(--seo-text-light)]">
                …or upload a .csv / .txt file:{" "}
                <input
                  type="file"
                  accept=".csv,.tsv,.txt"
                  onChange={(e) => e.target.files?.[0] && handleFile(e.target.files[0])}
                  className="text-sm"
                />
              </label>
            </>
          ) : null}

          {/* Bulk options */}
          {mode !== "single" ? (
            <div className="grid grid-cols-2 gap-3">
              <Field label={`URL limit (max ${effectiveMaxLimit})`}>
                <input
                  type="number"
                  min={1}
                  max={effectiveMaxLimit}
                  value={clampedLimit}
                  onChange={(e) =>
                    setLimit(Math.max(1, Math.min(effectiveMaxLimit, Number(e.target.value) || DEFAULT_LIMIT)))
                  }
                  className={inputClass}
                />
              </Field>
              <Field label={`Parallel workers (max ${MAX_CONCURRENCY})`}>
                <input
                  type="number"
                  min={1}
                  max={MAX_CONCURRENCY}
                  value={concurrency}
                  onChange={(e) =>
                    setConcurrency(Math.max(1, Math.min(MAX_CONCURRENCY, Number(e.target.value) || DEFAULT_CONCURRENCY)))
                  }
                  className={inputClass}
                />
              </Field>
            </div>
          ) : null}

          <Field label="Audit type">
            <select
              value={auditType}
              onChange={(e) => setAuditType(e.target.value as typeof auditType)}
              className={inputClass}
            >
              <option value="auto">Auto-Detect</option>
              <option value="course">Course</option>
              <option value="blog">Blog</option>
              <option value="general">General</option>
            </select>
          </Field>

          <div className="flex flex-col gap-2">
            <label className="flex items-center gap-2 text-sm text-[var(--seo-text)]">
              <input type="checkbox" checked={checkLinks} onChange={(e) => setCheckLinks(e.target.checked)} />
              Check links
            </label>
            <label className="flex items-center gap-2 text-sm text-[var(--seo-text)]">
              <input type="checkbox" checked={fetchPagespeed} onChange={(e) => setFetchPagespeed(e.target.checked)} />
              Fetch PageSpeed Insights (much slower, not recommended for bulk)
            </label>
          </div>

          {error ? (
            <div className="rounded-lg border border-[var(--seo-error-border)] bg-[var(--seo-error-bg)] px-3 py-2 text-sm text-[var(--seo-error)]">
              {error}
            </div>
          ) : null}

          {!running ? (
            <button
              type="submit"
              className="rounded-lg btn-gradient px-4 py-2 text-sm font-semibold text-white disabled:opacity-60"
            >
              {mode === "single" ? "Run Audit" : "Run Technical Audit"}
            </button>
          ) : (
            <button
              type="button"
              onClick={cancel}
              className="rounded-lg border border-[var(--seo-error-border)] bg-[var(--seo-error-bg)] px-4 py-2 text-sm font-semibold text-[var(--seo-error)]"
            >
              {mode === "single" ? "Cancel" : "Pause (resumable)"}
            </button>
          )}
        </form>
      </Card>

      <ChecklistExplainer />

      {/* Progress */}
      {phase === "resolving" ? (
        <Card className="mt-4">
          <p className="text-sm text-[var(--seo-text)]">Preparing audit (resolving URLs and site health)…</p>
        </Card>
      ) : null}

      {progress ? (
        <Card className="mt-4">
          <div className="mb-2 flex items-center justify-between text-sm">
            <span className="font-semibold text-[var(--seo-subheading)]">
              {phase === "done"
                ? "Audit complete"
                : phase === "idle"
                  ? "Paused"
                  : progress.totalChunks > 1
                    ? `Auditing… (batch ${progress.currentChunk} of ${progress.totalChunks})`
                    : "Auditing…"}
            </span>
            <span className="text-[var(--seo-text-light)]">
              {progress.completed} / {progress.total}
            </span>
          </div>
          <div className="h-2 w-full overflow-hidden rounded-full bg-[var(--seo-card-hover)]">
            <div
              className="h-2 rounded-full transition-all"
              style={{
                width: `${progress.total ? (progress.completed / progress.total) * 100 : 0}%`,
                background: "var(--seo-gradient)",
              }}
            />
          </div>
          <div className="mt-2 flex flex-wrap gap-4 text-xs text-[var(--seo-text-light)]">
            <span>✅ {progress.succeeded} ok</span>
            <span>⚠️ {progress.failed} failed</span>
            {resolvedCount ? (
              <span>
                {resolvedCount.found} found{resolvedCount.capped ? ` (capped to ${clampedLimit})` : ""}
              </span>
            ) : null}
            {progress.lastUrl ? <span className="truncate">Last: {progress.lastUrl}</span> : null}
          </div>
          {phase === "done" || (phase === "idle" && progress.completed > 0) ? (
            <button
              type="button"
              onClick={() => router.push("/results")}
              className="mt-3 rounded-lg btn-gradient px-4 py-2 text-sm font-semibold text-white"
            >
              View {progress.completed} results
            </button>
          ) : null}
        </Card>
      ) : null}
    </div>
  );
}

function BulkLimitNote({ limit }: { limit: number }) {
  return (
    <p className="-mt-1 text-xs text-[var(--seo-text-light)]">
      Up to <span className="font-semibold">{limit.toLocaleString()}</span> URLs per audit.
    </p>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  // Nest the control inside the <label> so the label is programmatically
  // associated with it (no need to thread an id/htmlFor through every caller).
  return (
    <label className="block">
      <span className="mb-1 block text-sm font-medium text-[var(--seo-subheading)]">{label}</span>
      {children}
    </label>
  );
}
