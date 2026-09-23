"use client";

import { useMemo, useState } from "react";
import { Card } from "@/components/ui";
import { useAudit } from "@/lib/state/AuditContext";
import { downloadCsv } from "@/lib/format";
import { detectFixTarget, type FixTarget } from "@/lib/fixSuggestable";
import type { AuditResult } from "@/lib/types";

/**
 * AI Fix Pack (M4 T4.3, reframed T4.1): bulk-draft ready-to-paste replacements
 * for a chosen element (meta title / description / H1) across every audited page
 * that has that issue, then export them as CSV for a Webflow copy-paste workflow.
 *
 * Client-orchestrated (like the crawl path) since there's no always-on worker:
 * it calls the existing /api/ai "fix-suggestion" endpoint per page with limited
 * concurrency and a hard page cap, so it stays within Groq's free-tier limits.
 */
const TARGETS: { value: Exclude<FixTarget, "og" | "alt">; label: string }[] = [
  { value: "title", label: "Meta title" },
  { value: "description", label: "Meta description" },
  { value: "h1", label: "H1 heading" },
];
const MAX_PAGES = 100;   // hard cap to protect the free Groq tier
const CONCURRENCY = 3;

interface FixRow {
  url: string;
  current: string;
  suggestion: string;
}

function currentValue(r: AuditResult, target: string): string {
  if (target === "title") return r.metadata?.title || "";
  if (target === "description") return r.metadata?.description || "";
  if (target === "h1") return r.heading_detail?.h1_text?.[0] || "";
  return "";
}

export function AiFixPackCard({ className = "" }: { className?: string }) {
  const { results, groqApiKey } = useAudit();
  const [target, setTarget] = useState<(typeof TARGETS)[number]["value"]>("title");
  const [rows, setRows] = useState<FixRow[]>([]);
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const running = progress !== null && progress.done < progress.total;

  // Pages whose issues include one matching the chosen fix target.
  const affected = useMemo(() => {
    const out: { r: AuditResult; issue: string }[] = [];
    for (const r of results) {
      const match = (r.all_issues || []).find((i) => detectFixTarget(i.issue) === target);
      if (match) out.push({ r, issue: match.issue });
    }
    return out;
  }, [results, target]);

  async function generate() {
    setError(null);
    setRows([]);
    const batch = affected.slice(0, MAX_PAGES);
    if (batch.length === 0) return;
    setProgress({ done: 0, total: batch.length });

    const collected: FixRow[] = [];
    let idx = 0;
    let done = 0;

    async function worker() {
      while (idx < batch.length) {
        const my = idx++;
        const { r, issue } = batch[my];
        try {
          const res = await fetch("/api/ai", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              action: "fix-suggestion",
              issue,
              pageContext: {
                url: r.url,
                title: r.metadata?.title,
                description: r.metadata?.description,
                h1: r.heading_detail?.h1_text?.[0],
                content_snippet: (r.content?.intro_paragraphs || []).join(" ").slice(0, 1500),
              },
              apiKey: groqApiKey || undefined,
            }),
          });
          const data = await res.json();
          if (data.ok && data.suggestion) {
            collected.push({ url: r.url, current: currentValue(r, target), suggestion: data.suggestion });
          }
        } catch {
          /* skip this page; keep going */
        } finally {
          done += 1;
          setProgress({ done, total: batch.length });
        }
      }
    }

    await Promise.all(Array.from({ length: Math.min(CONCURRENCY, batch.length) }, worker));
    setRows(collected.sort((a, b) => a.url.localeCompare(b.url)));
    if (collected.length === 0) setError("No suggestions were generated (check your Groq key in Settings).");
  }

  function exportCsv() {
    const label = TARGETS.find((t) => t.value === target)!.label;
    downloadCsv(
      `fix-pack-${target}.csv`,
      [["URL", `Current ${label}`, `Suggested ${label}`], ...rows.map((r) => [r.url, r.current, r.suggestion])],
    );
  }

  return (
    <Card className={className}>
      <h3 className="mb-1 text-sm font-semibold text-[var(--seo-subheading)]">AI Fix Pack</h3>
      <p className="mb-3 text-xs text-[var(--seo-muted)]">
        Bulk-draft ready-to-paste replacements for pages with the selected issue, then export them as
        CSV to paste into Webflow. Grounded in each page&apos;s own content.
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <select
          value={target}
          onChange={(e) => { setTarget(e.target.value as typeof target); setRows([]); setProgress(null); setError(null); }}
          disabled={running}
          className="rounded-lg border border-[var(--seo-border-strong)] bg-[var(--seo-card-bg)] px-3 py-1.5 text-sm text-[var(--seo-text)]"
        >
          {TARGETS.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
        </select>
        <span className="text-xs text-[var(--seo-muted)]">
          {affected.length} page{affected.length === 1 ? "" : "s"} affected
          {affected.length > MAX_PAGES ? ` (first ${MAX_PAGES} will be processed)` : ""}
        </span>
        <button
          type="button"
          onClick={generate}
          disabled={running || affected.length === 0}
          className="rounded-lg btn-gradient px-3 py-1.5 text-sm font-semibold text-white disabled:opacity-50"
        >
          {running ? `Drafting… ${progress?.done}/${progress?.total}` : "Generate fixes"}
        </button>
        {rows.length > 0 ? (
          <button
            type="button"
            onClick={exportCsv}
            className="rounded-lg border border-[var(--seo-border-strong)] px-3 py-1.5 text-sm font-medium text-[var(--seo-text)] hover:bg-[var(--seo-card-hover)]"
          >
            Export CSV ({rows.length})
          </button>
        ) : null}
      </div>
      {error ? <p className="mt-3 text-sm text-[var(--seo-error)]">{error}</p> : null}
      {rows.length > 0 ? (
        <div className="mt-3 max-h-[360px] overflow-auto">
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="sticky top-0 border-b border-[var(--seo-border)] bg-[var(--seo-card-bg)] text-[var(--seo-muted)]">
                <th className="py-1.5 pr-2">Page</th>
                <th className="py-1.5 pr-2">Current</th>
                <th className="py-1.5">Suggested</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.url} className="border-b border-[var(--table-row-border)] align-top">
                  <td className="max-w-[14rem] truncate py-1.5 pr-2 font-mono text-[var(--seo-text-light)]" title={r.url}>{r.url}</td>
                  <td className="max-w-[16rem] py-1.5 pr-2 text-[var(--seo-muted)]">{r.current || "(none)"}</td>
                  <td className="py-1.5 text-[var(--seo-text)]">{r.suggestion}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </Card>
  );
}
