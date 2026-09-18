"use client";

import { useState } from "react";
import DOMPurify from "dompurify";
import Link from "next/link";
import { useAudit } from "@/lib/state/AuditContext";
import { Card, DifficultyBadge, EmptyState, HelpSection, IssueRow, PageHeader, ScoreBadge, StatusPill, TabBar } from "@/components/ui";
import { GlobeIcon } from "@/components/icons";
import { difficultyBreakdown } from "@/lib/difficulty";
import { getThematicIssues, getTopIssuesByImpact } from "@/lib/aggregate";
import { WEIGHTS } from "@/lib/scoring";
import { scoreColor } from "@/lib/format";
import type { ChecklistItem } from "@/lib/types";
import { CHECK_DEFS, GROUP_HELP, GROUP_LABELS } from "@/lib/checklistDefs";
import { useSelectedChecks } from "@/lib/useSelectedChecks";
import { LinksView } from "@/components/detail/LinksView";
import { HeadingsView } from "@/components/detail/HeadingsView";
import { PerformanceView } from "@/components/detail/PerformanceView";
import { AiSummaryCard } from "@/components/AiSummaryCard";

// "Recommendations" used to be its own 8th tab, but it just re-rendered the
// top-10 issues through the same IssueRow the Issues tab already uses —
// folded into a card at the top of Issues instead of a separate tab a user
// had to click through 7 others to reach.
const TABS = [
  "Overview",
  "Technical",
  "Issues",
  "Links",
  "Headings",
  "Content",
  "Performance",
] as const;
type Tab = (typeof TABS)[number];

const CHECK_DESCRIPTIONS: Record<string, string> = Object.fromEntries(
  CHECK_DEFS.map((c) => [c.id, c.description]),
);

function ChecklistGroupCard({
  group,
  items,
}: {
  group: ChecklistItem["group"];
  items: ChecklistItem[];
}) {
  return (
    <Card>
      <h3 className="mb-1 text-sm font-semibold text-[var(--seo-subheading)]">
        {GROUP_LABELS[group]} ({items.length})
      </h3>
      <HelpSection>{GROUP_HELP[group]}</HelpSection>
      <div className="mt-3 flex flex-col">
        {items.map((item) => (
          <div
            key={item.id}
            title={CHECK_DESCRIPTIONS[item.id]}
            className="flex items-center justify-between gap-3 border-b border-[var(--seo-border)] py-2 last:border-0"
          >
            <div className="min-w-0">
              <div className="truncate text-sm font-medium text-[var(--seo-subheading)]">
                {item.label}
              </div>
              {item.detail ? (
                <div className="truncate text-xs text-[var(--seo-muted)]">{item.detail}</div>
              ) : null}
            </div>
            <StatusPill status={item.status} />
          </div>
        ))}
      </div>
    </Card>
  );
}

function BoolBadge({ ok, yes = "Pass", no = "Fail" }: { ok: boolean; yes?: string; no?: string }) {
  return (
    <span
      className="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium"
      style={{
        color: ok ? "var(--seo-success)" : "var(--seo-error)",
        backgroundColor: ok ? "var(--seo-success-bg)" : "var(--seo-error-bg)",
      }}
    >
      {ok ? yes : no}
    </span>
  );
}

// A labelled row (label left, value/badge right) for the structured Technical tab.
function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-[var(--seo-border)] py-2 text-sm last:border-0">
      <span className="text-[var(--seo-text-light)]">{label}</span>
      <span className="max-w-[60%] truncate text-right font-medium text-[var(--seo-subheading)]">
        {children}
      </span>
    </div>
  );
}

function formatValue(v: unknown): string {
  if (v === null || v === undefined || v === "") return "N/A";
  if (Array.isArray(v)) return String(v.length);
  if (typeof v === "object") {
    const entries = Object.entries(v as Record<string, unknown>).filter(
      ([k]) => k !== "issues" && !k.startsWith("_"),
    );
    if (entries.length === 0) return "N/A";
    return entries
      .map(([k, val]) => {
        if (Array.isArray(val)) return `${k}: ${val.length}`;
        if (val && typeof val === "object") return `${k}: …`;
        if (val === null || val === undefined || val === "") return `${k}: N/A`;
        return `${k}: ${val}`;
      })
      .join(", ");
  }
  return String(v);
}

function KeyValueGrid({ data }: { data: Record<string, unknown> }) {
  const entries = Object.entries(data || {}).filter(
    ([k]) => k !== "issues" && !k.startsWith("_"),
  );
  return (
    <div className="grid grid-cols-1 gap-x-8 gap-y-2 sm:grid-cols-2">
      {entries.map(([k, v]) => (
        <div key={k} className="flex items-center justify-between border-b border-[var(--seo-border)] py-1.5 text-sm">
          <span className="text-[var(--seo-text-light)] capitalize">
            {k.replace(/_/g, " ")}
          </span>
          {typeof v === "boolean" ? (
            <BoolBadge ok={v} />
          ) : (
            <span className="max-w-[60%] truncate text-right font-medium text-[var(--seo-subheading)]" title={formatValue(v)}>
              {formatValue(v)}
            </span>
          )}
        </div>
      ))}
    </div>
  );
}

export default function DetailPage() {
  const { results, selectedUrlIndex, setSelectedUrlIndex, groqApiKey } = useAudit();
  const [tab, setTab] = useState<Tab>("Overview");
  const [urlCopied, setUrlCopied] = useState(false);
  const { selected: selectedChecks } = useSelectedChecks();

  if (results.length === 0) {
    return (
      <div>
        <PageHeader icon={<GlobeIcon size={18} />} title="URL Detail" />
        <EmptyState title="No audits yet" hint="Run an audit to see details here." />
      </div>
    );
  }

  const idx = Math.min(selectedUrlIndex, results.length - 1);
  const r = results[idx];
  const breakdown = r.score_breakdown || {};
  const issues = r.all_issues || [];
  const grouped = getThematicIssues(issues);
  const topIssues = getTopIssuesByImpact(issues, 10);
  // Grounds the "Suggest a fix" action (IssueRow -> FixSuggestionButton) in
  // this page's real content instead of letting the model invent facts.
  const fixPageContext = {
    url: r.url,
    title: r.metadata?.title,
    description: r.metadata?.description,
    h1: r.heading_detail?.h1_text,
    content_snippet: (r.content?.intro_paragraphs || []).join(" ").slice(0, 1500),
  };

  return (
    <div>
      <Link
        href="/results"
        className="mb-3 inline-flex items-center gap-1 text-sm font-medium text-[var(--seo-accent)] hover:underline"
      >
        ← Back to results
      </Link>
      <PageHeader icon={<GlobeIcon size={18} />} title="URL Detail" subtitle={r.url} />

      <div className="mb-4 flex items-center gap-2">
        {results.length > 1 ? (
          <select
            value={idx}
            onChange={(e) => setSelectedUrlIndex(Number(e.target.value))}
            className="min-w-0 flex-1 rounded-lg border border-[var(--seo-border-strong)] bg-[var(--seo-card-bg)] px-3 py-2 text-sm text-[var(--seo-text)]"
          >
            {results.map((res, i) => (
              <option key={res.url} value={i}>
                {res.url}
              </option>
            ))}
          </select>
        ) : (
          <div className="min-w-0 flex-1 truncate rounded-lg border border-[var(--seo-border-strong)] bg-[var(--seo-card-bg)] px-3 py-2 text-sm text-[var(--seo-text)]">
            {r.url}
          </div>
        )}
        <button
          type="button"
          onClick={() => {
            navigator.clipboard?.writeText(r.url).then(() => {
              setUrlCopied(true);
              setTimeout(() => setUrlCopied(false), 1500);
            }).catch(() => {});
          }}
          className="shrink-0 rounded-lg border border-[var(--seo-border-strong)] px-3 py-2 text-sm font-medium text-[var(--seo-subheading)] hover:bg-[var(--seo-card-hover)]"
        >
          {urlCopied ? "Copied ✓" : "Copy"}
        </button>
      </div>

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <ScoreBadge score={r.seo_score ?? 0} />
        <span className="text-sm text-[var(--seo-text-light)]">
          Status {r.status_code ?? "N/A"} · {issues.length} issues · {r.response_time?.toFixed?.(2) ?? "N/A"}s
        </span>
        {issues.length > 0
          ? (() => {
              const b = difficultyBreakdown(issues);
              return (
                <span className="flex items-center gap-1.5 text-xs">
                  <span className="text-[var(--seo-muted)]">Fix effort:</span>
                  {b.Easy > 0 ? <DifficultyBadge difficulty="Easy" /> : null}
                  {b.Medium > 0 ? <DifficultyBadge difficulty="Medium" /> : null}
                  {b.Hard > 0 ? <DifficultyBadge difficulty="Hard" /> : null}
                  <span className="text-[var(--seo-muted)]">
                    ({b.Easy} / {b.Medium} / {b.Hard})
                  </span>
                </span>
              );
            })()
          : null}
      </div>

      <TabBar tabs={TABS} active={tab} onChange={setTab} />

      {tab === "Overview" ? (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {topIssues.length > 0 ? (
            <Card className="lg:col-span-2">
              <div className="mb-1 flex items-center justify-between">
                <h3 className="text-sm font-semibold text-[var(--seo-subheading)]">
                  Top Priority Fixes
                </h3>
                <button
                  type="button"
                  onClick={() => setTab("Issues")}
                  className="text-xs font-medium text-[var(--seo-accent)] hover:underline"
                >
                  View all {issues.length} issues →
                </button>
              </div>
              {topIssues.slice(0, 3).map((issue, i) => (
                <IssueRow key={i} issue={issue} pageContext={fixPageContext} groqApiKey={groqApiKey} />
              ))}
            </Card>
          ) : null}

          <Card>
            <h3 className="mb-3 text-sm font-semibold text-[var(--seo-subheading)]">
              Score Breakdown
            </h3>
            <div className="flex flex-col gap-2">
              {Object.entries(breakdown).map(([cat, val]) => {
                const v = Math.max(0, Math.min(100, val as number));
                return (
                  <div key={cat}>
                    <div className="flex justify-between text-xs text-[var(--seo-text-light)]">
                      <span className="capitalize">
                        {cat.replace(/_/g, " ")} ({Math.round((WEIGHTS[cat] || 0) * 100)}%)
                      </span>
                      <span style={{ color: scoreColor(v) }}>{Math.round(v)}</span>
                    </div>
                    <div className="mt-0.5 h-1.5 w-full rounded-full bg-[var(--seo-card-hover)]">
                      <div
                        className="h-1.5 rounded-full"
                        style={{ width: `${v}%`, backgroundColor: scoreColor(v) }}
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          </Card>

          <Card>
            <h3 className="mb-3 text-sm font-semibold text-[var(--seo-subheading)]">
              SERP Preview
            </h3>
            <div className="rounded-lg border border-[var(--seo-border)] p-3">
              <div className="truncate text-xs text-[var(--seo-success)]">{r.final_url || r.url}</div>
              <div className="mt-0.5 truncate text-lg text-[#1A0DAB]">
                {r.metadata?.title || "Untitled page"}
              </div>
              <div className="mt-0.5 line-clamp-2 text-sm text-[var(--seo-text-light)]">
                {r.metadata?.description || "No meta description set."}
              </div>
            </div>
            <div className="mt-3 flex gap-3 text-xs text-[var(--seo-text-light)]">
              <span>Title: {r.metadata?.title_length ?? 0} chars</span>
              <span>Description: {r.metadata?.description_length ?? 0} chars</span>
              <span>OG tags: {r.metadata?.has_og_tags ? "Yes" : "No"}</span>
            </div>
          </Card>

          <AiSummaryCard
            className="lg:col-span-2"
            cacheKey={r.url}
            url={r.url}
            seoScore={r.seo_score ?? 0}
            issues={issues}
          />
        </div>
      ) : null}

      {tab === "Technical" ? (
        r.technical_audit_checklist ? (
          (() => {
            const filteredGroups = {
              crawlability: r.technical_audit_checklist.groups.crawlability.filter((c) => selectedChecks.has(c.id)),
              on_page: r.technical_audit_checklist.groups.on_page.filter((c) => selectedChecks.has(c.id)),
              site_health: r.technical_audit_checklist.groups.site_health.filter((c) => selectedChecks.has(c.id)),
            };
            const shown = filteredGroups.crawlability.length + filteredGroups.on_page.length + filteredGroups.site_health.length;
            const shownChecks = [...filteredGroups.crawlability, ...filteredGroups.on_page, ...filteredGroups.site_health];
            const shownPass = shownChecks.filter((c) => c.status === "pass").length;
            const shownWarning = shownChecks.filter((c) => c.status === "warning").length;
            const shownFail = shownChecks.filter((c) => c.status === "fail").length;

            const adv = r.advanced || {};
            const tech = adv.technical_seo || r.technical_seo || {};
            const hdr = adv.http_headers_data || {};
            const sh = r.site_health || {};
            const social = adv.social_preview || {};
            const mobile = r.mobile_audit || {};
            const hreflang: { lang?: string; url?: string }[] = adv.hreflang_tags || [];
            const schemaTypes: string[] = adv.schema_types || [];
            const redirectChain: string[] = r.redirect_analysis?.chain || r.redirect_chain || [];

            return (
              <div className="flex flex-col gap-6">
                <Card>
                  <div className="flex flex-wrap items-center gap-4 text-sm">
                    <span className="font-semibold text-[var(--seo-subheading)]">
                      Technical SEO Audit: {shown} of {r.technical_audit_checklist.summary.total} checks shown
                    </span>
                    <span className="flex items-center gap-1">
                      <StatusPill status="pass" /> {shownPass}
                    </span>
                    <span className="flex items-center gap-1">
                      <StatusPill status="warning" /> {shownWarning}
                    </span>
                    <span className="flex items-center gap-1">
                      <StatusPill status="fail" /> {shownFail}
                    </span>
                  </div>
                  {shown < r.technical_audit_checklist.summary.total ? (
                    <p className="mt-2 text-xs text-[var(--seo-muted)]">
                      {r.technical_audit_checklist.summary.total - shown} check(s) hidden. Adjust on the
                      Technical Audit page → Customize checks.
                    </p>
                  ) : null}
                </Card>

                {shown === 0 ? (
                  <EmptyState
                    title="All checks hidden"
                    hint="Go to the Technical Audit page → Customize checks and select at least one check."
                  />
                ) : null}

                {/* Crawlability: can search engines reach and route this page correctly? */}
                {filteredGroups.crawlability.length > 0 ? (
                  <section>
                    <h3 className="mb-3 text-sm font-bold uppercase tracking-wide text-[var(--seo-heading)]">
                      Crawlability
                    </h3>
                    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                      <ChecklistGroupCard group="crawlability" items={filteredGroups.crawlability} />
                      <div className="flex flex-col gap-4">
                        {redirectChain.length > 1 ? (
                          <Card>
                            <h4 className="mb-2 text-sm font-semibold text-[var(--seo-subheading)]">
                              Redirect Chain ({redirectChain.length - 1} hop{redirectChain.length > 2 ? "s" : ""})
                            </h4>
                            <div className="flex flex-col gap-1 text-sm">
                              {redirectChain.map((u, i) => (
                                <div key={`${u}-${i}`} className="flex items-center gap-2">
                                  <span className="text-xs text-[var(--seo-muted)]">{i + 1}.</span>
                                  <span className="truncate text-[var(--seo-text-light)]">{u}</span>
                                </div>
                              ))}
                            </div>
                          </Card>
                        ) : null}
                        {hreflang.length ? (
                          <Card>
                            <h4 className="mb-2 text-sm font-semibold text-[var(--seo-subheading)]">
                              International Targeting (hreflang)
                            </h4>
                            <div className="flex flex-col">
                              {hreflang.map((h, i) => (
                                <div
                                  key={`${h.lang}-${i}`}
                                  className="flex items-center justify-between gap-3 border-b border-[var(--seo-border)] py-1.5 text-sm last:border-0"
                                >
                                  <span className="font-mono text-xs text-[var(--seo-accent)]">{h.lang}</span>
                                  <span className="max-w-[70%] truncate text-right text-[var(--seo-text-light)]">{h.url}</span>
                                </div>
                              ))}
                            </div>
                          </Card>
                        ) : null}
                        {redirectChain.length <= 1 && hreflang.length === 0 ? (
                          <Card>
                            <p className="text-sm text-[var(--seo-muted)]">
                              No redirect hops or hreflang tags to report for this page.
                            </p>
                          </Card>
                        ) : null}
                      </div>
                    </div>
                  </section>
                ) : null}

                {/* On-Page: does the page's own markup communicate correctly to search, social, and mobile? */}
                {filteredGroups.on_page.length > 0 ? (
                  <section>
                    <h3 className="mb-3 text-sm font-bold uppercase tracking-wide text-[var(--seo-heading)]">
                      On-Page
                    </h3>
                    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                      <ChecklistGroupCard group="on_page" items={filteredGroups.on_page} />
                      <div className="flex flex-col gap-4">
                        <Card>
                          <h4 className="mb-2 text-sm font-semibold text-[var(--seo-subheading)]">
                            Schema Audit (Structured Data)
                          </h4>
                          {schemaTypes.length ? (
                            <div className="mb-2 flex flex-wrap gap-1.5">
                              {schemaTypes.map((t, i) => (
                                <span
                                  key={`${t}-${i}`}
                                  className="pill"
                                  style={{ color: "var(--seo-accent)", backgroundColor: "var(--seo-accent-light)" }}
                                >
                                  {t}
                                </span>
                              ))}
                            </div>
                          ) : (
                            <p className="mb-2 text-sm text-[var(--seo-muted)]">No structured data found.</p>
                          )}
                          <Row label="Schema parse errors">
                            <BoolBadge ok={!(adv.schema_errors?.length)} yes="None" no={`${adv.schema_errors?.length || 0}`} />
                          </Row>
                          <Row label="Favicon">
                            <BoolBadge ok={!!adv.has_favicon} yes="Present" no="Missing" />
                          </Row>
                          <Row label="Charset">{adv.charset_value || "N/A"}</Row>
                          <Row label="HTML lang">{adv.lang_attr || "N/A"}</Row>
                        </Card>

                        <Card>
                          <h4 className="mb-2 text-sm font-semibold text-[var(--seo-subheading)]">
                            Mobile Responsiveness
                          </h4>
                          <Row label="Mobile friendly">
                            <BoolBadge ok={!!mobile.is_mobile_friendly} yes="Yes" no="No" />
                          </Row>
                          <Row label="Mobile score">
                            {mobile.mobile_score != null ? `${mobile.mobile_score}/100` : "N/A"}
                          </Row>
                          <Row label="Checks passed">
                            {mobile.total_checks != null ? `${mobile.passed_checks ?? 0}/${mobile.total_checks}` : "N/A"}
                          </Row>
                          <button
                            type="button"
                            onClick={() => setTab("Performance")}
                            className="mt-2 text-xs font-medium text-[var(--seo-accent)] hover:underline"
                          >
                            View full mobile audit → Performance tab
                          </button>
                        </Card>

                        <Card>
                          <h4 className="mb-2 text-sm font-semibold text-[var(--seo-subheading)]">
                            Social Preview (Open Graph)
                          </h4>
                          <div className="overflow-hidden rounded-lg border border-[var(--seo-border)]">
                            {social.og_image ? (
                              // eslint-disable-next-line @next/next/no-img-element
                              <img
                                src={social.og_image}
                                alt="Open Graph preview"
                                className="h-36 w-full object-cover"
                                onError={(e) => ((e.target as HTMLImageElement).style.display = "none")}
                              />
                            ) : (
                              <div className="flex h-36 w-full items-center justify-center bg-[var(--seo-card-bg-alt)] text-xs text-[var(--seo-muted)]">
                                No og:image set
                              </div>
                            )}
                            <div className="p-3">
                              <div className="text-xs text-[var(--seo-muted)]">{social.og_site_name || r.metadata?.title}</div>
                              <div className="truncate text-sm font-semibold text-[var(--seo-subheading)]">
                                {social.og_title || r.metadata?.title || "Untitled"}
                              </div>
                              <div className="line-clamp-2 text-xs text-[var(--seo-text-light)]">
                                {social.og_description || r.metadata?.description || "No description set."}
                              </div>
                            </div>
                          </div>
                          <div className="mt-2">
                            <Row label="Twitter Card">{social.twitter_card_type || "N/A"}</Row>
                          </div>
                        </Card>
                      </div>
                    </div>
                  </section>
                ) : null}

                {/* Site Health: is the underlying domain/server trustworthy and fast? */}
                {filteredGroups.site_health.length > 0 ? (
                  <section>
                    <h3 className="mb-3 text-sm font-bold uppercase tracking-wide text-[var(--seo-heading)]">
                      Site Health
                    </h3>
                    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                      <ChecklistGroupCard group="site_health" items={filteredGroups.site_health} />
                      <div className="flex flex-col gap-4">
                        <Card>
                          <h4 className="mb-2 text-sm font-semibold text-[var(--seo-subheading)]">
                            Domain &amp; Protocol Detail
                          </h4>
                          <div className="grid grid-cols-1 gap-x-8 sm:grid-cols-2">
                            <div>
                              <Row label="SSL certificate">
                                {sh.ssl?.valid ? (
                                  <BoolBadge ok yes={sh.ssl.days_left != null ? `Valid, ${sh.ssl.days_left}d left` : "Valid"} />
                                ) : (
                                  <BoolBadge ok={false} no="Invalid / none" />
                                )}
                              </Row>
                              <Row label="HTTPS protocol">
                                {sh.http2?.http_version ? (
                                  <BoolBadge
                                    ok={["HTTP/2", "HTTP/3"].includes(sh.http2.http_version)}
                                    yes={sh.http2.http_version}
                                    no={sh.http2.http_version}
                                  />
                                ) : "N/A"}
                              </Row>
                              <Row label="www / non-www">
                                <BoolBadge ok={sh.www_redirect?.consolidated !== false} yes="Consolidated" no="Split" />
                              </Row>
                              <Row label="Domain age">
                                {sh.domain_age?.age_years != null ? `${sh.domain_age.age_years} years` : "Unknown"}
                              </Row>
                              <Row label="robots.txt">
                                <BoolBadge ok={sh.robots?.allowed !== false} yes="Allows crawl" no="Blocks page" />
                              </Row>
                            </div>
                            <div>
                              <Row label="Sitemap">
                                {sh.sitemap?.exists ? `${sh.sitemap.url_count ?? 0} URLs` : "Not found"}
                              </Row>
                              <Row label="Readability">
                                {sh.readability?.fk_grade != null ? `Grade ${sh.readability.fk_grade}` : "N/A"}
                              </Row>
                              <Row label="Page size">
                                {tech.page_size_kb != null ? `${tech.page_size_kb} KB` : "N/A"}
                              </Row>
                              <Row label="DOM elements">{tech.dom_elements ?? "N/A"}</Row>
                              <Row label="Scripts (external)">
                                {tech.script_count != null
                                  ? `${tech.script_count} (${tech.external_script_count ?? 0} external)`
                                  : "N/A"}
                              </Row>
                              {tech.has_mixed_content ? (
                                <Row label="Mixed content">
                                  <BoolBadge ok={false} no={`${tech.mixed_content_count ?? 0} insecure`} />
                                </Row>
                              ) : null}
                            </div>
                          </div>
                          <p className="mt-2 text-xs text-[var(--seo-muted)]">
                            Email-deliverability records (SPF/DMARC/MX) are shown as informational checklist
                            items above; they don&rsquo;t affect the SEO score.
                          </p>
                        </Card>

                        <Card>
                          <h4 className="mb-2 text-sm font-semibold text-[var(--seo-subheading)]">
                            Security &amp; Response Headers
                          </h4>
                          <Row label="HSTS (Strict-Transport-Security)">
                            <BoolBadge ok={!!hdr.has_hsts} yes="Present" no="Missing" />
                          </Row>
                          <Row label="X-Frame-Options">
                            <BoolBadge ok={!!hdr.has_x_frame_options} yes="Present" no="Missing" />
                          </Row>
                          <Row label="X-Content-Type-Options">
                            <BoolBadge ok={!!hdr.has_x_content_type_options} yes="Present" no="Missing" />
                          </Row>
                          <Row label="Content-Security-Policy">
                            <BoolBadge ok={!!hdr.has_csp} yes="Present" no="Missing" />
                          </Row>
                          <Row label="Compression">
                            <BoolBadge ok={!!hdr.has_compression} yes="Enabled" no="Off" />
                          </Row>
                          <Row label="Cache-Control">{hdr.cache_control || "N/A"}</Row>
                          <Row label="Server">{hdr.server || "N/A"}</Row>
                        </Card>
                      </div>
                    </div>
                  </section>
                ) : null}
              </div>
            );
          })()
        ) : (
          <EmptyState title="Technical audit checklist unavailable" hint="Re-run the audit to generate it." />
        )
      ) : null}

      {tab === "Issues" ? (
        <div className="flex flex-col gap-4">
          {topIssues.length > 0 ? (
            <Card>
              <h3 className="mb-1 text-sm font-semibold text-[var(--seo-subheading)]">
                Top Issues by Impact
              </h3>
              {topIssues.slice(0, 5).map((issue, i) => (
                <IssueRow key={i} issue={issue} pageContext={fixPageContext} groqApiKey={groqApiKey} />
              ))}
            </Card>
          ) : null}
          {Object.entries(grouped).map(([theme, themeIssues]) => (
            <Card key={theme}>
              <h3 className="mb-1 text-sm font-semibold text-[var(--seo-subheading)]">
                {theme} ({themeIssues.length})
              </h3>
              {themeIssues.map((issue, i) => (
                <IssueRow key={i} issue={issue} pageContext={fixPageContext} groqApiKey={groqApiKey} />
              ))}
            </Card>
          ))}
          {issues.length === 0 ? <EmptyState title="No issues found" /> : null}
        </div>
      ) : null}

      {tab === "Links" ? <LinksView result={r} /> : null}

      {tab === "Headings" ? <HeadingsView result={r} /> : null}

      {tab === "Content" ? (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <Card>
            <h3 className="mb-3 text-sm font-semibold text-[var(--seo-subheading)]">Content</h3>
            <KeyValueGrid
              data={Object.fromEntries(
                Object.entries(r.content || {}).filter(
                  ([k]) => !["intro_paragraphs", "conclusion_paragraphs", "intro_paragraphs_html", "conclusion_paragraphs_html"].includes(k),
                ),
              )}
            />
          </Card>
          <Card className="lg:col-span-2">
            <div className="mb-3 flex items-center justify-between">
              <h3 className="text-sm font-semibold text-[var(--seo-subheading)]">
                Body Content Preview
              </h3>
              <div className="flex items-center gap-3 text-xs text-[var(--seo-text-light)]">
                <span className="inline-flex items-center gap-1">
                  <span className="inline-block h-2 w-2 rounded-full" style={{ backgroundColor: "#1D4ED8" }} />
                  Internal link
                </span>
                <span className="inline-flex items-center gap-1">
                  <span className="inline-block h-2 w-2 rounded-full" style={{ backgroundColor: "#7C3AED" }} />
                  External link
                </span>
              </div>
            </div>
            {(r.content?.intro_paragraphs_html?.length || r.content?.conclusion_paragraphs_html?.length) ? (
              <div className="flex flex-col gap-3 text-sm leading-relaxed text-[var(--seo-text)]">
                {(r.content?.intro_paragraphs_html || []).map((html: string, i: number) => (
                  <p key={`intro-${i}`} dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(html) }} />
                ))}
                {(r.content?.conclusion_paragraphs_html || []).map((html: string, i: number) => (
                  <p key={`concl-${i}`} dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(html) }} />
                ))}
              </div>
            ) : (
              <p className="text-sm text-[var(--seo-muted)]">
                No intro/conclusion paragraph text captured for this page.
              </p>
            )}
          </Card>
        </div>
      ) : null}

      {tab === "Performance" ? <PerformanceView result={r} /> : null}
    </div>
  );
}
