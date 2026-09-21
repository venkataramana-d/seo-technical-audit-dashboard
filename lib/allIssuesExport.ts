// Detailed CSV export for the Explorer view.
//
// Two pure, React-free builders that turn an AuditResult[] into CSV strings the
// caller downloads:
//
// 1. buildFilteredSummaryCsv - one row per URL (a flat per-page summary, like
//    the existing Results CSV but scoped to whatever the Explorer is currently
//    showing).
// 2. buildAllIssuesCsv - the actionable one: one row per issue INSTANCE. When an
//    issue carries multiple `affected` elements (Issue.affected?: {value; detail?}[]),
//    each offending element becomes its own row, so the export tells you exactly
//    WHERE every problem lives (which image, which link, which element).
//
// Every cell is run through sanitizeCsvCell (lib/format.ts) to neutralize CSV/
// Excel formula injection, then quoted, matching downloadCsv()'s wire format.

import { sanitizeCsvCell } from "@/lib/format";
import type { AuditResult, Issue } from "@/lib/types";

/** Serialize a matrix of cells into a CSV string, sanitizing + quoting each
 * cell exactly like lib/format.ts::downloadCsv does (so behavior stays
 * identical whether a CSV is built here or there). */
function toCsv(rows: string[][]): string {
  return rows
    .map((row) => row.map((cell) => `"${sanitizeCsvCell(cell).replace(/"/g, '""')}"`).join(","))
    .join("\n");
}

const SUMMARY_COLUMNS = [
  "URL", "Type", "Status", "SEO Score", "Response Time (s)", "Total Issues",
  "Critical", "High", "Medium", "Low", "Title", "Title Length", "Meta Desc Length",
  "H1 Count", "Word Count", "Images", "Images Missing Alt", "Internal Links",
  "Broken Internal", "External Links", "Broken External", "Indexable",
] as const;

/**
 * Per-URL summary CSV: one row per audited page. All nested access is guarded
 * with optional chaining and string fallbacks so a partially-populated or
 * errored AuditResult never throws.
 */
export function buildFilteredSummaryCsv(results: AuditResult[]): string {
  const rows: string[][] = [[...SUMMARY_COLUMNS]];
  for (const r of results || []) {
    const issues = r?.all_issues || [];
    const sev = (s: string) => issues.filter((i) => i?.severity === s).length;
    const meta = r?.metadata || {};
    const head = r?.headings || {};
    const cont = r?.content || {};
    const imgs = r?.images || {};
    const idx = r?.indexability || {};
    const il = r?.internal_links || {};
    const el = r?.external_links || {};

    rows.push([
      r?.url ?? "",
      r?.audit_type ? r.audit_type[0].toUpperCase() + r.audit_type.slice(1) : "",
      String(r?.status_code ?? ""),
      String(r?.seo_score ?? 0),
      String(Math.round((r?.response_time ?? 0) * 100) / 100),
      String(issues.length),
      String(sev("Critical")),
      String(sev("High")),
      String(sev("Medium")),
      String(sev("Low") + sev("Warning")),
      String(meta.title ?? ""),
      String(meta.title_length ?? ""),
      String(meta.description_length ?? ""),
      String(head.h1_count ?? ""),
      String(cont.word_count ?? ""),
      String(imgs.total_images ?? ""),
      String(imgs.missing_alt_count ?? ""),
      String(il.total_links ?? ""),
      String(il.broken_count ?? ""),
      String(el.total_links ?? ""),
      String(el.broken_count ?? ""),
      String(idx.is_indexable ?? ""),
    ]);
  }
  return toCsv(rows);
}

const ISSUE_COLUMNS = [
  "URL", "Issue", "Category", "Severity", "Impact", "Effort",
  "Affected Element", "Detail", "Recommendation",
] as const;

/** Build the shared columns for one issue on one page (everything except the
 * per-affected-element Affected/Detail pair). */
function issueBase(url: string, issue: Issue): string[] {
  return [
    url,
    String(issue?.issue ?? ""),
    String(issue?.category ?? ""),
    String(issue?.severity ?? ""),
    String(issue?.impact_score ?? ""),
    String(issue?.effort ?? ""),
  ];
}

/**
 * All-issues CSV: one row per issue INSTANCE. An issue with N affected elements
 * expands into N rows (one per offending element), each carrying that element's
 * value + detail. An issue with no affected elements produces a single row with
 * empty Affected Element / Detail cells. This is what makes the export
 * actionable - it says exactly where each issue is, not just that it exists.
 */
export function buildAllIssuesCsv(results: AuditResult[]): string {
  const rows: string[][] = [[...ISSUE_COLUMNS]];
  for (const r of results || []) {
    const url = r?.url ?? "";
    for (const issue of r?.all_issues || []) {
      const base = issueBase(url, issue);
      const recommendation = String(issue?.recommendation ?? "");
      const affected = issue?.affected || [];
      if (affected.length === 0) {
        rows.push([...base, "", "", recommendation]);
        continue;
      }
      for (const a of affected) {
        rows.push([...base, String(a?.value ?? ""), String(a?.detail ?? ""), recommendation]);
      }
    }
  }
  return toCsv(rows);
}
