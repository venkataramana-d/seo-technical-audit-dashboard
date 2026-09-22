import type { AuditResult } from "@/lib/types";

export interface LinkEntry {
  url: string;
  anchor_text: string;
  anchor_type: string;
  link_category?: "page" | "pdf" | "download" | "image" | string;
  location?: "nav" | "header" | "footer" | "sidebar" | "breadcrumb" | "body" | string;
  is_dofollow: boolean;
  is_nofollow: boolean;
  is_sponsored?: boolean;
  is_ugc?: boolean;
  is_broken?: boolean | null;
  is_redirect?: boolean | null;
  health?: string;
  opens_new_tab: boolean;
  has_noopener: boolean;
  has_noreferrer?: boolean;
  missing_target?: boolean;
  is_weak_anchor?: boolean;
  status_code?: number | null;
  status_label?: string;
  redirect_path?: string[] | null;
  response_time_ms?: number | null;
  content_type?: string | null;
  sourceUrl: string;
}

export interface SpecialLinkEntry {
  href: string;
  anchor_text: string;
  kind: "mailto" | "tel" | "anchor" | "javascript";
  location: string;
  sourceUrl: string;
}

export function flattenSpecialLinks(results: AuditResult[]): SpecialLinkEntry[] {
  return results.flatMap((r) => {
    const special = r.special_links || {};
    return Object.values(special).flatMap((list) =>
      (list || []).map((l) => ({ ...l, sourceUrl: r.url })),
    );
  });
}

export function flattenLinks(
  results: AuditResult[],
  kind: "internal" | "external",
): LinkEntry[] {
  return results.flatMap((r) => {
    const data = kind === "internal" ? r.internal_links : r.external_links;
    const links: LinkEntry[] = data?.links || [];
    return links.map((l) => ({ ...l, sourceUrl: r.url }));
  });
}

export function getBaseDomain(url: string): string {
  try {
    const host = new URL(url).hostname.toLowerCase();
    return host.startsWith("www.") ? host.slice(4) : host;
  } catch {
    return "";
  }
}

// Mirrors modules/link_auditor.py DOMAIN_CATEGORIES / categorize_domain
const DOMAIN_CATEGORIES: Record<string, Set<string>> = {
  social: new Set([
    "facebook.com", "twitter.com", "x.com", "linkedin.com", "instagram.com",
    "youtube.com", "tiktok.com", "pinterest.com", "reddit.com", "snapchat.com",
  ]),
  news: new Set([
    "bbc.com", "cnn.com", "nytimes.com", "theguardian.com", "reuters.com",
    "apnews.com", "bloomberg.com", "forbes.com", "wsj.com", "techcrunch.com",
    "businessinsider.com", "entrepreneur.com",
  ]),
  academic: new Set([
    "scholar.google.com", "researchgate.net", "academia.edu", "jstor.org",
    "pubmed.ncbi.nlm.nih.gov", "springer.com", "ieee.org", "ssrn.com",
  ]),
  government: new Set(["gov", "mil", "europa.eu"]),
  reference: new Set([
    "wikipedia.org", "wikimedia.org", "britannica.com", "investopedia.com",
    "merriam-webster.com",
  ]),
  tech: new Set([
    "github.com", "stackoverflow.com", "developer.mozilla.org", "docs.python.org",
    "aws.amazon.com", "cloud.google.com", "docs.microsoft.com", "npmjs.com",
  ]),
};

export function categorizeDomain(domain: string): string {
  const d = (domain || "").toLowerCase().replace(/^www\./, "");
  for (const [cat, domains] of Object.entries(DOMAIN_CATEGORIES)) {
    if (domains.has(d)) return cat[0].toUpperCase() + cat.slice(1);
    if (cat === "government") {
      for (const tld of domains) {
        if (d.endsWith(`.${tld}`) || d === tld) return "Government";
      }
    }
  }
  return "Other";
}

export function anchorTextDistribution(links: LinkEntry[], topN = 20) {
  const counts = new Map<string, { count: number; isWeak: boolean }>();
  for (const l of links) {
    const anchor = (l.anchor_text || "").trim();
    if (!anchor) continue;
    const entry = counts.get(anchor) || { count: 0, isWeak: !!l.is_weak_anchor };
    entry.count += 1;
    counts.set(anchor, entry);
  }
  const total = links.length || 1;
  return [...counts.entries()]
    .sort((a, b) => b[1].count - a[1].count)
    .slice(0, topN)
    .map(([anchor, { count, isWeak }]) => ({
      anchor,
      count,
      isWeak,
      pct: Math.round((count / total) * 1000) / 10,
    }));
}

export function orphanAndLowLinkPages(results: AuditResult[]) {
  const pageUrls = new Set(results.map((r) => r.url));
  const inbound = new Map<string, number>();
  for (const url of pageUrls) inbound.set(url, 0);
  for (const r of results) {
    for (const l of r.internal_links?.links || []) {
      if (inbound.has(l.url)) inbound.set(l.url, (inbound.get(l.url) || 0) + 1);
    }
  }
  const orphan: string[] = [];
  const lowLink: string[] = [];
  for (const [url, count] of inbound.entries()) {
    if (count === 0) orphan.push(url);
    else if (count < 3) lowLink.push(url);
  }
  return { orphan, lowLink };
}

export interface DomainStat {
  domain: string;
  category: string;
  count: number;
  dofollow: number;
  nofollow: number;
  broken: number;
}

export function externalDomainBreakdown(links: LinkEntry[], topN = 15): DomainStat[] {
  const byDomain = new Map<string, DomainStat>();
  for (const l of links) {
    const domain = getBaseDomain(l.url);
    if (!domain) continue;
    const stat = byDomain.get(domain) || {
      domain,
      category: categorizeDomain(domain),
      count: 0,
      dofollow: 0,
      nofollow: 0,
      broken: 0,
    };
    stat.count += 1;
    if (l.is_dofollow) stat.dofollow += 1;
    if (l.is_nofollow) stat.nofollow += 1;
    if (l.is_broken) stat.broken += 1;
    byDomain.set(domain, stat);
  }
  return [...byDomain.values()].sort((a, b) => b.count - a.count).slice(0, topN);
}

export function linkHealthCounts(links: LinkEntry[]) {
  let ok = 0, broken = 0, redirect = 0, unknown = 0;
  for (const l of links) {
    if (l.is_broken) broken++;
    else if (l.is_redirect) redirect++;
    else if (l.health === "unknown" || l.health === undefined) unknown++;
    else ok++;
  }
  return { ok, broken, redirect, unknown };
}

export function securityGaps(links: LinkEntry[]): LinkEntry[] {
  // Only a missing noopener is an actual security concern (reverse tabnabbing).
  // noreferrer is a privacy/analytics choice (it strips the Referer header), not a
  // security gap, so a link with rel="noopener" only must NOT be flagged.
  return links.filter((l) => l.opens_new_tab && !l.has_noopener);
}

// Was this specific link actually HTTP-checked (validateLinks was on), or is its
// health/status just the unchecked default? Surfaced in the UI as a certainty label
// rather than a numeric "confidence score": a 404 is a 404, not a probability.
export function linkCertainty(link: LinkEntry): "Verified" | "Not Checked" {
  return link.status_code !== null && link.status_code !== undefined ? "Verified" : "Not Checked";
}

// Deterministic priority scoring (NOT a machine-learned score): combines issue
// severity with reach (internal links affect crawl budget/link equity on your own
// site; homepage-adjacent links are seen by more crawl paths).
export function priorityScore(link: LinkEntry, kind: "internal" | "external", isHomepage: boolean): number {
  let score = 0;
  if (link.is_broken) score = 70;
  else if (link.is_redirect) score = 40;
  else return 0;
  if (kind === "internal") score += 20;
  if (isHomepage) score += 10;
  return Math.min(100, score);
}

export interface ExecutiveSummary {
  linkHealthScore: number;
  totalLinks: number;
  criticalCount: number;
  brokenCount: number;
  redirectCount: number;
  securityGapCount: number;
  weakAnchorCount: number;
  orphanCount: number;
  quickWins: string[];
  topPriorityFixes: string[];
}

// Rule-based summary computed from already-gathered stats, not LLM-generated prose.
// Labeled as such in the UI; wiring a real LLM for natural-language write-ups would
// need an API key (see project notes).
export function buildExecutiveSummary(
  allLinks: LinkEntry[],
  orphanCount: number,
): ExecutiveSummary {
  const health = linkHealthCounts(allLinks);
  const gaps = securityGaps(allLinks);
  const weak = allLinks.filter((l) => l.is_weak_anchor);
  const total = allLinks.length || 1;
  const healthScore = Math.round(((health.ok + health.unknown * 0.5) / total) * 100);
  const criticalCount = health.broken;

  const quickWins: string[] = [];
  if (weak.length > 0) quickWins.push(`Rewrite ${weak.length} weak anchor text link(s) (e.g. "click here") with descriptive text.`);
  if (gaps.length > 0) quickWins.push(`(advisory) Add rel="noopener" to ${gaps.length} new-tab link(s) — modern browsers already imply this.`);
  if (orphanCount > 0) quickWins.push(`Add internal links to ${orphanCount} orphan page(s) with zero inbound links.`);

  const topPriorityFixes: string[] = [];
  if (health.broken > 0) topPriorityFixes.push(`Fix ${health.broken} broken link(s): direct crawl and user-experience impact.`);
  if (health.redirect > 0) topPriorityFixes.push(`Update ${health.redirect} redirecting link(s) to point straight to the final URL.`);

  return {
    linkHealthScore: Math.max(0, Math.min(100, healthScore)),
    totalLinks: allLinks.length,
    criticalCount,
    brokenCount: health.broken,
    redirectCount: health.redirect,
    securityGapCount: gaps.length,
    weakAnchorCount: weak.length,
    orphanCount,
    quickWins,
    topPriorityFixes,
  };
}

// ── Color coding system (consistent across dashboard, tables, filters, exports) ──
export type StatusColor = "passed" | "info" | "warning" | "high" | "critical" | "ignored";

export const STATUS_COLOR_HEX: Record<StatusColor, string> = {
  passed: "#10B981",
  info: "#0369A1",
  warning: "#D97706",
  high: "#EA580C",
  critical: "#DC2626",
  ignored: "#94A3B8",
};

export const STATUS_COLOR_LABEL: Record<StatusColor, string> = {
  passed: "🟢 Passed",
  info: "🔵 Information",
  warning: "🟡 Warning",
  high: "🟠 High Priority",
  critical: "🔴 Critical",
  ignored: "⚪ Ignored",
};

export interface LinkExplanation {
  issueName: string;
  status: StatusColor;
  severity: "Critical" | "High" | "Medium" | "Low" | "Passed";
  whatIsIt: string;
  whyImportant: string;
  rootCause: string;
  seoImpact: string;
  userImpact: string;
  recommendedFix: string;
  htmlExample?: string;
}

function rootCauseFor(link: LinkEntry): string {
  const label = link.status_label as string | undefined;
  const code = link.status_code;
  if (label?.includes("Timeout")) return "The destination server did not respond in time, likely slow, overloaded, or unreachable.";
  if (label?.includes("SSL")) return "The destination has an invalid, expired, or misconfigured SSL certificate.";
  if (label?.includes("Connection Error")) return "Could not connect to the destination, likely DNS failure or the domain/server is down.";
  if (code === 404 || code === 410) return "The page was deleted, moved without a redirect, or the URL was typed/generated incorrectly.";
  if (code === 403 || code === 401) return "The destination is blocking access, often a site that rejects automated/bot requests, or a page requiring login.";
  if (code === 429) return "The destination is rate-limiting requests: too many checks were made in a short window.";
  if (code && code >= 500) return "The destination server encountered an internal error while handling the request.";
  if (code && code >= 300 && code < 400) return "The link points to a URL that has since moved to a different location.";
  return "Unknown: the link could not be reached or classified.";
}

// Deterministic (rule-based) per-link explanation, mirrors the shape asked for by
// a "why is this an issue / how to fix it" audit report, without an LLM call.
export function explainLink(link: LinkEntry, kind: "internal" | "external"): LinkExplanation {
  if (link.is_broken) {
    const code = link.status_code;
    return {
      issueName: `Broken Link (${link.status_label || code || "error"})`,
      status: "critical",
      severity: "Critical",
      whatIsIt: `This ${kind} link returns ${link.status_label || "an error"} instead of a working page.`,
      whyImportant: "Broken links waste crawl budget, break the user journey, and signal poor site maintenance to both users and search engines.",
      rootCause: rootCauseFor(link),
      seoImpact:
        kind === "internal"
          ? "Wastes crawl budget, breaks internal link equity flow, and can orphan sections of your site from search engines."
          : "Reduces trust and content-quality signals; visitors bouncing off a dead link increases exit rate on this page.",
      userImpact: "Visitors who click this link land on an error page instead of the content they expected.",
      recommendedFix:
        "Update the href to the correct, working URL. If the destination content no longer exists, remove the link or replace it with the closest relevant page.",
      htmlExample: `<a href="https://example.com/correct-page">${link.anchor_text || "Descriptive link text"}</a>`,
    };
  }
  if (link.is_redirect) {
    const chain = link.redirect_path || [];
    return {
      issueName: "Unnecessary Redirect",
      status: "warning",
      severity: "Medium",
      whatIsIt: `This link points to a URL that redirects${chain.length > 1 ? ` through ${chain.length - 1} hop(s)` : ""} before reaching its final destination.`,
      whyImportant: "Every redirect hop adds latency and dilutes a small amount of link equity passed to the final page.",
      rootCause: rootCauseFor(link),
      seoImpact: "Each hop in a redirect chain slightly dilutes link equity and slows crawling; long chains can even be dropped by crawlers.",
      userImpact: "Visitors experience a small added delay while the browser follows the redirect.",
      recommendedFix: "Update the href to point directly at the final destination URL shown below, skipping the redirect hop(s).",
      htmlExample: chain.length
        ? `<a href="${chain[chain.length - 1]}">${link.anchor_text || "Descriptive link text"}</a>`
        : undefined,
    };
  }
  if (link.opens_new_tab && !link.has_noopener) {
    return {
      issueName: "Advisory: rel=\"noopener\" not set",
      status: "info",
      severity: "Low",
      whatIsIt: 'This link opens in a new tab (target="_blank") without an explicit rel="noopener".',
      whyImportant:
        "Since 2021 all modern browsers imply noopener for target=\"_blank\", so the historic reverse-tabnabbing risk is already mitigated by default. Setting it explicitly is a defence-in-depth nicety, not a live vulnerability.",
      rootCause: "The target=\"_blank\" attribute was added without an explicit rel=\"noopener\" attribute.",
      seoImpact: "None. This is not an SEO issue and does not affect rankings or crawling.",
      userImpact: "None in modern browsers, which already isolate the new tab. Only very old browsers lacked the implicit protection.",
      recommendedFix: 'Optional: add rel="noopener" to links using target="_blank" for explicit clarity and to cover legacy browsers.',
      htmlExample: `<a href="${link.url}" target="_blank" rel="noopener">${link.anchor_text || "Link text"}</a>`,
    };
  }
  if (link.is_weak_anchor) {
    return {
      issueName: "Weak / Generic Anchor Text",
      status: "info",
      severity: "Low",
      whatIsIt: `The anchor text "${link.anchor_text}" is generic and doesn't describe the destination.`,
      whyImportant: "Descriptive anchor text is a contextual relevance signal for search engines and helps users scanning the page.",
      rootCause: "Generic phrasing (e.g. \"click here\", \"read more\") was used instead of descriptive text.",
      seoImpact: "Missed opportunity for a small contextual relevance signal to the linked page's topic.",
      userImpact: "Visitors scanning the page or using a screen reader can't tell where the link leads without additional context.",
      recommendedFix: "Replace the anchor text with a short, descriptive phrase naming the destination content.",
      htmlExample: `<a href="${link.url}">${kind === "internal" ? "Descriptive Page Title" : "Descriptive Source Name"}</a>`,
    };
  }
  return {
    issueName: "No Issues Detected",
    status: "passed",
    severity: "Passed",
    whatIsIt: "This link resolved successfully and has no detected behavior or attribute issues.",
    whyImportant: "Healthy links maintain crawlability and a good user experience.",
    rootCause: "N/A",
    seoImpact: "None. Link is functioning as expected.",
    userImpact: "None. Link works as expected.",
    recommendedFix: "No action needed.",
  };
}

export interface DuplicateAnchorGroup {
  anchor: string;
  destinations: string[];
}

// "Duplicate anchor" in the sense professional audit tools flag: the SAME anchor
// text used to link to DIFFERENT destinations: a real ambiguity issue, distinct
// from just "this anchor text appears more than once" (which is normal, e.g. nav
// links repeated across pages).
export function duplicateAnchors(links: LinkEntry[]): DuplicateAnchorGroup[] {
  const byAnchor = new Map<string, Set<string>>();
  for (const l of links) {
    const anchor = (l.anchor_text || "").trim();
    if (!anchor || l.is_weak_anchor) continue;
    const set = byAnchor.get(anchor) || new Set<string>();
    set.add(l.url);
    byAnchor.set(anchor, set);
  }
  return [...byAnchor.entries()]
    .filter(([, dest]) => dest.size > 1)
    .map(([anchor, dest]) => ({ anchor, destinations: [...dest] }))
    .sort((a, b) => b.destinations.length - a.destinations.length);
}
