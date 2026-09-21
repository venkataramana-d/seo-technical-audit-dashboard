"use client";

import type { AuditResult } from "@/lib/types";
import { buildFilteredSummaryCsv, buildAllIssuesCsv } from "@/lib/allIssuesExport";

/** Trigger a client-side file download for an in-memory string payload. No
 * network round-trip - the browser already holds the full results array. */
function downloadText(filename: string, content: string, mime: string) {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

/**
 * Export toolbar for the Explorer view. Every button acts on the CURRENTLY
 * FILTERED rows (`filtered`) so the export always matches what the user is
 * looking at; `all` is accepted so the note can show the filtered/total split.
 *
 * - Export view (CSV): per-URL summary of the filtered rows.
 * - Export all issues (CSV): one row per issue instance (per offending element).
 * - Export JSON: raw filtered AuditResult[].
 */
export function ExplorerExportBar({ filtered, all }: { filtered: AuditResult[]; all: AuditResult[] }) {
  const urlCount = filtered?.length ?? 0;
  const issueCount = (filtered || []).reduce((n, r) => n + (r?.all_issues?.length ?? 0), 0);
  const totalCount = all?.length ?? 0;
  const isFiltered = totalCount !== urlCount;
  const disabled = urlCount === 0;

  const buttonClass =
    "rounded-lg border border-[var(--seo-border-strong)] px-3 py-1.5 text-sm font-medium text-[var(--seo-text)] hover:bg-[var(--seo-card-hover)] disabled:opacity-60 disabled:cursor-not-allowed";

  return (
    <div className="flex flex-wrap items-center gap-3">
      <span className="text-sm font-semibold text-[var(--seo-subheading)]">Export</span>
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          disabled={disabled}
          onClick={() =>
            downloadText("seo-explorer-view.csv", buildFilteredSummaryCsv(filtered), "text/csv;charset=utf-8;")
          }
          title="Per-URL summary of the currently filtered rows"
          className={buttonClass}
        >
          Export view (CSV)
        </button>
        <button
          type="button"
          disabled={disabled}
          onClick={() =>
            downloadText("seo-all-issues.csv", buildAllIssuesCsv(filtered), "text/csv;charset=utf-8;")
          }
          title="One row per issue instance, with the exact affected element"
          className={buttonClass}
        >
          Export all issues (CSV)
        </button>
        <button
          type="button"
          disabled={disabled}
          onClick={() =>
            downloadText("seo-export.json", JSON.stringify(filtered, null, 2), "application/json")
          }
          title="Raw audit data for the currently filtered rows"
          className={buttonClass}
        >
          Export JSON
        </button>
      </div>
      <span className="text-xs text-[var(--seo-muted)]">
        {disabled
          ? "No URLs match the current view"
          : `${urlCount} URL${urlCount === 1 ? "" : "s"} · ${issueCount} issue${issueCount === 1 ? "" : "s"} in current view${isFiltered ? ` (of ${totalCount} total)` : ""}`}
      </span>
    </div>
  );
}

export default ExplorerExportBar;
