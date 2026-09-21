import type { Issue } from "@/lib/types";

export interface AiSummary {
  ok: boolean;
  explanation?: string;
  top_actions?: string[];
  error?: string;
}

export interface AiSummaryCacheEntry {
  fingerprint: string;
  summary: AiSummary;
}

/** Cheap, deterministic fingerprint of the inputs an AI Summary was generated
 * from. Not cryptographic - it only needs to change whenever the substantive
 * input changes (score, which issues, their severities) so a cached summary
 * is correctly invalidated after a re-audit, and stay stable otherwise so
 * reopening the same result doesn't re-spend an API call. */
export function fingerprintForSummary(seoScore: number | undefined, issues: Issue[]): string {
  const basis = `${seoScore ?? 0}|${issues.map((i) => `${i.issue}:${i.severity}`).sort().join(",")}`;
  let hash = 0;
  for (let i = 0; i < basis.length; i++) {
    hash = (hash * 31 + basis.charCodeAt(i)) | 0;
  }
  return `${issues.length}-${hash}`;
}
