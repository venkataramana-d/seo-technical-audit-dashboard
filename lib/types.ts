/** One offending element the backend attached to an issue, telling the UI
 * exactly WHERE the issue is (image src, link target, title text, canonical
 * URL, redirect hop, etc.). `value` is the element itself; `detail` is optional
 * extra context (e.g. "123 chars", the anchor text, the status code). */
export interface AffectedElement {
  value: string;
  detail?: string;
}

export interface Issue {
  issue: string;
  category: string;
  severity: string;
  recommendation: string;
  impact_score: number;
  effort: string;
  /** The exact offending elements for this issue, if the backend attached them. */
  affected?: AffectedElement[];
}

export type ChecklistStatus = "pass" | "warning" | "fail" | "info";

export interface ChecklistItem {
  id: string;
  label: string;
  group: "crawlability" | "on_page" | "site_health";
  status: ChecklistStatus;
  detail: string;
}

export interface TechnicalAuditChecklist {
  groups: {
    crawlability: ChecklistItem[];
    on_page: ChecklistItem[];
    site_health: ChecklistItem[];
  };
  checks: ChecklistItem[];
  summary: { total: number; pass: number; warning: number; fail: number; info: number };
}

// The Python /api/audit endpoint returns the full audit_url() result almost
// verbatim (see modules/auditor.py). Nested category dicts are intentionally
// left loosely typed here: they're rendered read-only in the UI and their
// exact shape is defined in the Python modules, not duplicated here.
export interface AuditResult {
  url: string;
  audit_timestamp: string;
  status_code: number | null;
  audit_type: string;
  fetch_error?: string | null;
  response_time: number;
  redirect_count: number;
  redirect_chain?: Record<string, unknown>[];
  final_url: string;
  metadata: Record<string, any>;
  headings: Record<string, any>;
  heading_detail: Record<string, any>;
  canonical: Record<string, any>;
  indexability: Record<string, any>;
  url_structure: Record<string, any>;
  content: Record<string, any>;
  images: Record<string, any>;
  image_detail: Record<string, any>;
  advanced: Record<string, any>;
  site_health: Record<string, any>;
  redirect_analysis: Record<string, any>;
  internal_links: Record<string, any>;
  external_links: Record<string, any>;
  special_links?: Record<string, any[]>;
  special_link_counts?: Record<string, number>;
  course_audit?: Record<string, any> | null;
  blog_audit?: Record<string, any> | null;
  http_headers: Record<string, any>;
  technical_seo: Record<string, any>;
  mobile_audit?: Record<string, any>;
  pagespeed?: Record<string, any>;
  ssl_warning?: string | null;
  seo_score: number;
  score_breakdown: Record<string, number>;
  all_issues: Issue[];
  technical_audit_checklist?: TechnicalAuditChecklist;
}

export interface AuditOptions {
  auditType: "auto" | "course" | "blog" | "general";
  checkLinks: boolean;
  validateLinks: boolean;
  fetchPagespeed: boolean;
}

export interface NavFilter {
  kind: string;
  key: string;
}
