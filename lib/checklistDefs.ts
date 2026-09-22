// Static metadata for the 35-check "Technical SEO Audit" checklist, mirrors
// the check ids/labels/groups produced by
// modules/technical_audit_checklist.py::build_technical_audit_checklist().
// Used for: the plain-English explainer card, the check-selection panel, and
// filtering the checklist tab on the detail page. Keep this list's ids in
// sync with that Python module, see agents.md.

import type { ChecklistItem } from "@/lib/types";

export interface CheckDef {
  id: string;
  label: string;
  group: ChecklistItem["group"];
  /** One-sentence plain-English explanation of what this check verifies and why it matters. */
  description: string;
}

export const GROUP_LABELS: Record<ChecklistItem["group"], string> = {
  crawlability: "Crawlability",
  on_page: "On-Page",
  site_health: "Site Health",
};

export const GROUP_HELP: Record<ChecklistItem["group"], string> = {
  crawlability:
    "Can search engines find, fetch, and follow links to this page at all? Covers robots.txt rules, HTTP status, redirects, sitemaps, canonical tags, and indexability signals: the foundation everything else depends on.",
  on_page:
    "Does the page itself have the content and markup search engines and readers expect? Covers titles, descriptions, headings, images, word count, schema markup, and mobile-friendliness.",
  site_health:
    "Is the underlying domain trustworthy and technically sound? Covers SSL/HTTPS, DNS/email authentication (SPF/DMARC/MX), security headers, domain age, and server protocol support (HTTP/2).",
};

export const CHECK_DEFS: CheckDef[] = [
  // Crawlability (12)
  { id: "robots_check", label: "robots.txt allows crawling", group: "crawlability",
    description: "Confirms robots.txt doesn't block this page from search engine crawlers (including Googlebot specifically)." },
  { id: "http_status_check", label: "HTTP status healthy", group: "crawlability",
    description: "Checks the page returns a healthy 2xx status. 3xx redirects and 4xx/5xx errors hurt rankings and crawl budget." },
  { id: "redirect_check", label: "No excessive redirect chain", group: "crawlability",
    description: "Flags pages reached through multiple redirect hops. Each hop wastes crawl budget and slows visitors down." },
  { id: "broken_link_check", label: "No broken internal links", group: "crawlability",
    description: "Finds internal links pointing to pages that return errors. Broken links hurt user experience and waste link equity." },
  { id: "internal_links_check", label: "Has internal links", group: "crawlability",
    description: "Verifies other pages on the site link to this one. Pages with zero internal links are hard for search engines to discover." },
  { id: "sitemap_validate", label: "Valid XML sitemap", group: "crawlability",
    description: "Confirms the site's sitemap.xml exists, parses correctly, and isn't full of duplicate or malformed entries." },
  { id: "canonical_check", label: "Canonical tag present & self-referencing", group: "crawlability",
    description: "Flags only conflicting canonical tags (more than one). A missing canonical is fine — Google self-canonicalizes — and a canonical pointing elsewhere is often intentional." },
  { id: "meta_robots_check", label: "Indexable (meta robots / X-Robots-Tag)", group: "crawlability",
    description: "Makes sure nothing (meta tag or HTTP header) is accidentally telling search engines not to index this page." },
  { id: "hreflang_check", label: "Hreflang tags valid (if present)", group: "crawlability",
    description: "For multi-language sites, checks hreflang tags include an x-default fallback so unmatched languages still resolve correctly." },
  { id: "ttfb_check", label: "Time to First Byte", group: "crawlability",
    description: "Measures how fast the server starts responding. Slow TTFB delays everything else and signals server-side performance issues." },
  { id: "url_structure_check", label: "Clean URL structure", group: "crawlability",
    description: "Flags overly long URLs, uppercase letters, or messy query parameters that hurt readability and can cause duplicate-content issues." },
  { id: "canonical_loop_check", label: "No canonical redirect loop", group: "crawlability",
    description: "Detects canonical tags that point to each other in a loop or long chain instead of resolving directly to one final URL." },

  // On-Page (11)
  { id: "title_check", label: "Title tag present & well-sized", group: "on_page",
    description: "Checks the page has a title tag (the headline shown in search results). The ~30-60 character range is advisory pixel guidance for how much shows in results, not a hard limit — length alone is not flagged." },
  { id: "meta_description_check", label: "Meta description present & well-sized", group: "on_page",
    description: "Checks the page has a meta description (the preview text shown in search results). The ~150-160 character range is advisory pixel guidance, not a hard limit — length alone is not flagged." },
  { id: "heading_check", label: "Heading structure valid", group: "on_page",
    description: "Reviews the heading (H1-H6) outline. Multiple H1 tags are valid in HTML5 and fine for Google, so a single H1 is not required; skipped levels are advisory for accessibility." },
  { id: "image_alt_check", label: "Images have alt text", group: "on_page",
    description: "Checks every image has descriptive alt text, important for accessibility and how images rank in image search." },
  { id: "word_count_check", label: "Sufficient content depth", group: "on_page",
    description: "Flags thin content: pages under ~300 words rarely rank well and may not fully answer a searcher's question." },
  { id: "readability_check", label: "Readable content", group: "on_page",
    description: "Scores how easy the writing is to read (grade level). Overly complex sentences can hurt engagement." },
  { id: "schema_check", label: "Structured data (JSON-LD) valid", group: "on_page",
    description: "Checks for JSON-LD schema markup with no parse errors. Powers rich results like star ratings and breadcrumbs in search." },
  { id: "og_check", label: "Open Graph & Twitter Card tags complete", group: "on_page",
    description: "Verifies social-sharing preview tags are set, controls how the page looks when shared on social media." },
  { id: "viewport_check", label: "Mobile viewport meta tag", group: "on_page",
    description: "Confirms the page declares a mobile viewport so it renders correctly (not zoomed/squished) on phones." },
  { id: "lang_check", label: "HTML lang attribute set", group: "on_page",
    description: "Checks the page declares its language, helps screen readers, translation tools, and search engines." },
  { id: "content_freshness_check", label: "Content freshness signal present", group: "on_page",
    description: "Looks for a last-modified date or update signal. Freshness matters more for time-sensitive topics." },

  // Site Health (12)
  { id: "ssl_check", label: "Valid SSL certificate", group: "site_health",
    description: "Confirms the site has a valid, unexpired SSL certificate, required for HTTPS and browser trust." },
  { id: "domain_age_check", label: "Domain age", group: "site_health",
    description: "Reports how old the domain is (via WHOIS): informational context, very new domains take time to build trust." },
  { id: "mixed_content_check", label: "No mixed content", group: "site_health",
    description: "Detects insecure http:// resources (images, scripts) loading on an https:// page, which triggers browser security warnings." },
  { id: "https_enforcement_check", label: "HTTP redirects to HTTPS", group: "site_health",
    description: "Verifies visitors who type the http:// version are automatically redirected to the secure https:// version." },
  { id: "security_headers_check", label: "Security headers present", group: "site_health",
    description: "Checks for standard security headers (HSTS, X-Frame-Options, X-Content-Type-Options) that protect visitors." },
  { id: "spf_check", label: "SPF record configured", group: "site_health",
    description: "Informational only (does not affect the SEO score): SPF is an email-deliverability DNS record, not a search-ranking signal. Shown for domain context." },
  { id: "dmarc_check", label: "DMARC record configured", group: "site_health",
    description: "Informational only (does not affect the SEO score): DMARC is email anti-spoofing policy, not a search-ranking signal. Shown for domain context." },
  { id: "mx_records_check", label: "MX records configured", group: "site_health",
    description: "Informational only (does not affect the SEO score): MX records govern email delivery, not search ranking. Shown for domain context." },
  { id: "favicon_check", label: "Favicon present", group: "site_health",
    description: "Checks for a favicon: the small icon shown in browser tabs and bookmarks, a minor trust/branding signal." },
  { id: "dns_health_check", label: "Overall DNS/email health", group: "site_health",
    description: "Informational only (does not affect the SEO score): a combined summary of the SPF, DMARC, and MX results above, for email-security context." },
  { id: "www_redirect_check", label: "www/non-www consolidated", group: "site_health",
    description: "Checks that the www and non-www versions of the site don't both resolve independently, which can split ranking signals." },
  { id: "http2_check", label: "HTTP/2 or HTTP/3 support", group: "site_health",
    description: "Checks the server supports a modern HTTP protocol version, which loads multiple resources faster than HTTP/1.1." },
];

export const CHECK_IDS = CHECK_DEFS.map((c) => c.id);

export function checksByGroup(): Record<ChecklistItem["group"], CheckDef[]> {
  return {
    crawlability: CHECK_DEFS.filter((c) => c.group === "crawlability"),
    on_page: CHECK_DEFS.filter((c) => c.group === "on_page"),
    site_health: CHECK_DEFS.filter((c) => c.group === "site_health"),
  };
}
