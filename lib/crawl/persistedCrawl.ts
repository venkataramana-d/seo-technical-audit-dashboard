// Browser-driven persisted crawl (Vercel-only architecture - no always-on
// worker). The browser discovers URLs, audits each page via the existing
// serverless audit endpoint, and streams each result into the database through
// /api/crawls "ingest", then "finalize" runs the site-wide aggregation pass and
// scores. It runs while the page is open (navigating away stops it, same as the
// Technical Audit orchestrator) - matching Vercel's per-request function model.

async function postAudit<T>(action: string, body: Record<string, unknown>): Promise<T> {
  const res = await fetch("/api/audit-pipeline", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, ...body }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || "Audit request failed.");
  return data as T;
}

async function postCrawls<T>(action: string, body: Record<string, unknown>): Promise<T> {
  const res = await fetch("/api/crawls", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, ...body }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || "Request failed.");
  return data as T;
}

export type CrawlProgress = { discovered: number; done: number; currentUrl: string };

export async function runPersistedCrawl(opts: {
  crawlId: number;
  rootUrl: string;
  maxPages: number;
  maxDepth: number;
  robotsMode: string;
  concurrency?: number;
  onProgress?: (p: CrawlProgress) => void;
}): Promise<{ pages: number }> {
  const { crawlId, rootUrl, maxPages, maxDepth, robotsMode } = opts;
  const concurrency = opts.concurrency ?? 4;

  // 1) Discover URLs - one server-side BFS pass (capped), like the Technical
  //    Audit "Crawl" mode. Falls back to just the root if discovery yields none.
  let urls: string[] = [];
  try {
    const disc = await postAudit<{ urls?: string[] }>("crawl", {
      url: rootUrl,
      maxPages,
      maxDepth,
      robotsMode,
    });
    urls = Array.isArray(disc.urls) ? disc.urls : [];
  } catch {
    urls = [];
  }
  if (urls.length === 0) urls = [rootUrl];
  urls = urls.slice(0, maxPages);

  // 2) Audit + ingest each page with bounded concurrency (one audit function
  //    invocation per URL, well under Vercel's timeout - the proven pattern).
  const total = urls.length;
  let done = 0;
  let idx = 0;

  async function worker() {
    while (idx < urls.length) {
      const url = urls[idx++];
      try {
        const audit = await postAudit<Record<string, unknown>>("audit", { url, checkLinks: true });
        await postCrawls("ingest", {
          crawlId,
          url,
          outcome: { page: { url, status_code: (audit as { status_code?: number }).status_code ?? null, audit } },
        });
      } catch {
        // Best-effort: skip a page that fails to audit or ingest; the crawl
        // still finalizes with whatever persisted successfully.
      }
      done++;
      opts.onProgress?.({ discovered: total, done, currentUrl: url });
    }
  }

  await Promise.all(Array.from({ length: Math.min(concurrency, urls.length) }, () => worker()));

  // 3) Finalize - site-wide duplicate/orphan/redirect pass + health/SEO scores.
  await postCrawls("finalize", { crawlId, status: "completed" });
  return { pages: done };
}
