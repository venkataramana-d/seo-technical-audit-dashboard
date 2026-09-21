"use client";

import { useState } from "react";
import { Card } from "@/components/ui";
import { useAudit } from "@/lib/state/AuditContext";
import { fingerprintForSummary } from "@/lib/aiSummaryCache";
import type { Issue } from "@/lib/types";

/**
 * Shared "AI Summary" card: plain-English health summary + prioritized
 * actions via Groq (modules/ai_assist.py::explain_audit), cached per
 * `cacheKey` in AuditContext so reopening unchanged data doesn't re-spend an
 * API call. Used on both the Detail page (one URL) and the Results page's
 * Sitewide Summary (aggregated across every audited URL) - same UI, same
 * caching behavior, different `cacheKey`/`issues`/`contextLabel`.
 */
export function AiSummaryCard({
  cacheKey,
  url,
  seoScore,
  issues,
  contextLabel,
  className = "",
}: {
  cacheKey: string;
  url?: string;
  seoScore: number;
  issues: Issue[];
  contextLabel?: string;
  className?: string;
}) {
  const { groqApiKey, aiSummaryCache, setCachedAiSummary } = useAudit();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fingerprint = fingerprintForSummary(seoScore, issues);
  const cachedEntry = aiSummaryCache[cacheKey];
  const summary = cachedEntry?.fingerprint === fingerprint ? cachedEntry.summary : null;

  async function run() {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch("/api/ai", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action: "summary",
          url: url || "",
          seoScore,
          allIssues: issues,
          contextLabel,
          apiKey: groqApiKey || undefined,
        }),
      });
      const data = await res.json();
      if (data.ok) {
        setCachedAiSummary(cacheKey, { fingerprint, summary: data });
      } else {
        setError(data.error || "The assistant couldn't respond.");
      }
    } catch {
      setError("Request failed.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <Card className={className}>
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-[var(--seo-subheading)]">AI Summary</h3>
        <button
          type="button"
          onClick={run}
          disabled={loading}
          className="rounded-lg border border-[var(--seo-border-strong)] px-3 py-1.5 text-sm font-medium text-[var(--seo-subheading)] hover:bg-[var(--seo-card-hover)] disabled:opacity-50"
        >
          {loading ? "Summarising…" : "Generate AI Summary"}
        </button>
      </div>
      {!summary && !loading && !error ? (
        <p className="text-sm text-[var(--seo-muted)]">
          Get a plain-English health summary and prioritized fixes powered by Groq. Add
          a key in Settings, or the app falls back to the server default if configured.
        </p>
      ) : null}
      {error ? <p className="text-sm text-[var(--seo-error)]">{error}</p> : null}
      {summary ? (
        <div className="flex flex-col gap-3">
          <p className="text-sm text-[var(--seo-text)]">{summary.explanation}</p>
          {summary.top_actions && summary.top_actions.length > 0 ? (
            <ul className="list-disc pl-5 text-sm text-[var(--seo-text)]">
              {summary.top_actions.map((action, i) => (
                <li key={i}>{action}</li>
              ))}
            </ul>
          ) : null}
          <p className="text-xs text-[var(--seo-muted)]">Cached - click Generate to refresh.</p>
        </div>
      ) : null}
    </Card>
  );
}
