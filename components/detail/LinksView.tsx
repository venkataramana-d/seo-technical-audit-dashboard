"use client";

import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { AffectedList, Card, IssueExplanationGrid, locateAndHighlight, MetricCard, Modal, TabBar } from "@/components/ui";
import type { AffectedElement } from "@/lib/types";
import { downloadCsv } from "@/lib/format";
import {
  anchorTextDistribution,
  buildExecutiveSummary,
  duplicateAnchors,
  explainLink,
  externalDomainBreakdown,
  flattenLinks,
  flattenSpecialLinks,
  linkCertainty,
  linkHealthCounts,
  orphanAndLowLinkPages,
  priorityScore,
  securityGaps,
  STATUS_COLOR_HEX,
  type LinkEntry,
  type SpecialLinkEntry,
} from "@/lib/linkAnalysis";
import type { AuditResult } from "@/lib/types";
import { Cell, Legend, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";

const TABS = ["Overview", "Links", "Special Links", "Anchor Text", "Opportunities"] as const;
type Tab = (typeof TABS)[number];

const HEALTH_COLORS: Record<string, string> = {
  ok: "#10B981",
  broken: "#DC2626",
  redirect: "#D97706",
  unknown: "#94A3B8",
};
const FOLLOW_COLORS = ["var(--seo-accent)", "#94A3B8"];

// Deterministic per-link health color, mirrors linkHealthCounts()'s bucketing so
// row tints and the pie chart always agree on what counts as ok/broken/redirect/unknown.
function healthColorFor(l: LinkEntry): string {
  if (l.is_broken) return HEALTH_COLORS.broken;
  if (l.is_redirect) return HEALTH_COLORS.redirect;
  if (l.health === "unknown" || l.health == null) return HEALTH_COLORS.unknown;
  return HEALTH_COLORS.ok;
}

// Tints a MetricCard's background by overriding the --seo-card-bg custom property
// on a wrapping div: the shared Card component (in components/ui.tsx, not touched
// here) reads that variable, and CSS custom properties inherit through the DOM so
// this works without needing a style prop on MetricCard itself.
function cardTint(color: string): CSSProperties {
  return { "--seo-card-bg": `${color}18` } as CSSProperties;
}

const PAGE_SIZE = 50;

// Stable per-link identity used to track row selection, so a checked row stays
// tied to its data across pagination / filtering / sorting instead of to a
// positional array index (which points at whatever row currently sits there).
// sourceUrl + url disambiguates the same target linked from multiple pages;
// url alone is the fallback when sourceUrl is absent.
function linkKey(l: LinkEntry): string {
  return l.sourceUrl ? `${l.sourceUrl}\n${l.url}` : l.url;
}

type HealthFilter = "all" | "ok" | "broken" | "redirect";
type FollowFilter = "all" | "dofollow" | "nofollow";
type CategoryFilter = "all" | "page" | "pdf" | "download" | "image";
type LocationFilter = "all" | "nav" | "header" | "footer" | "sidebar" | "breadcrumb" | "body";
type TypeFilter = "all" | "internal" | "external";
type SortKey = "priority" | "response_time_ms" | "url" | "health";

interface LinkFilterPreset {
  health?: HealthFilter;
  follow?: FollowFilter;
  location?: LocationFilter;
  type?: TypeFilter;
}

export function LinksView({
  result,
  focusValue,
  focusSeq,
}: {
  result: AuditResult;
  /** A link target the Detail page wants revealed (jump-to-element). */
  focusValue?: string;
  /** Bumps each time a jump is requested, re-triggering even for the same value. */
  focusSeq?: number;
}) {
  const results = useMemo(() => [result], [result]);
  const [tab, setTab] = useState<Tab>("Overview");
  const [linkFilter, setLinkFilter] = useState<LinkFilterPreset>({});

  // Jump-to-element: when the Detail page requests a focus, switch to the Links
  // sub-tab (where the row-level table lives) and clear any preset filter so the
  // target isn't filtered out. The actual scroll + highlight happens in
  // LinkTable, which owns the search/pagination the row lives under. setState is
  // wrapped in requestAnimationFrame so it isn't called synchronously in the
  // effect body (repo rule: react-hooks/set-state-in-effect).
  const lastFocusSeq = useRef<number | undefined>(undefined);
  useEffect(() => {
    if (focusSeq == null || !focusValue) return;
    if (lastFocusSeq.current === focusSeq) return;
    lastFocusSeq.current = focusSeq;
    const raf = requestAnimationFrame(() => {
      setTab("Links");
      setLinkFilter({});
    });
    return () => cancelAnimationFrame(raf);
  }, [focusSeq, focusValue]);

  const internal = useMemo(
    () => flattenLinks(results, "internal").map((l) => ({ ...l, __kind: "internal" as const })),
    [results],
  );
  const external = useMemo(
    () => flattenLinks(results, "external").map((l) => ({ ...l, __kind: "external" as const })),
    [results],
  );
  const allLinks = useMemo(() => [...internal, ...external], [internal, external]);
  const specialLinks = useMemo(() => flattenSpecialLinks(results), [results]);
  const anchorDist = useMemo(() => anchorTextDistribution(allLinks), [allLinks]);
  const dupAnchors = useMemo(() => duplicateAnchors(allLinks), [allLinks]);
  const { orphan, lowLink } = useMemo(() => orphanAndLowLinkPages(results), [results]);
  const domainStats = useMemo(() => externalDomainBreakdown(external), [external]);
  const health = useMemo(() => linkHealthCounts(allLinks), [allLinks]);
  const gaps = useMemo(
    () => securityGaps(allLinks) as (LinkEntry & { __kind: "internal" | "external" })[],
    [allLinks],
  );
  const summary = useMemo(() => buildExecutiveSummary(allLinks, orphan.length), [allLinks, orphan.length]);

  // The exact links/pages behind each Executive-Summary fix, so the summary
  // points at "where" (mirrors the per-issue AffectedList used elsewhere) rather
  // than only counting. Values are URLs, which AffectedList renders as links.
  const weakLinks = useMemo(() => allLinks.filter((l) => l.is_weak_anchor), [allLinks]);
  const brokenLinks = useMemo(() => allLinks.filter((l) => l.is_broken), [allLinks]);
  const redirectLinks = useMemo(() => allLinks.filter((l) => l.is_redirect), [allLinks]);
  const asAffected = (links: LinkEntry[], detail: (l: LinkEntry) => string): AffectedElement[] =>
    links.slice(0, 200).map((l) => ({ value: l.url, detail: detail(l) }));

  const topPriorityItems = useMemo<{ label: string; affected: AffectedElement[] }[]>(() => {
    const items: { label: string; affected: AffectedElement[] }[] = [];
    if (brokenLinks.length > 0)
      items.push({
        label: `Fix ${brokenLinks.length} broken link(s): direct crawl and user-experience impact.`,
        affected: asAffected(brokenLinks, (l) => `${l.status_code ?? "broken"} · ${l.anchor_text || "no anchor"}`),
      });
    if (redirectLinks.length > 0)
      items.push({
        label: `Update ${redirectLinks.length} redirecting link(s) to point straight to the final URL.`,
        affected: asAffected(redirectLinks, (l) => `${l.status_code ?? "redirect"} · ${l.anchor_text || "no anchor"}`),
      });
    return items;
  }, [brokenLinks, redirectLinks]);

  const quickWinItems = useMemo<{ label: string; affected: AffectedElement[] }[]>(() => {
    const items: { label: string; affected: AffectedElement[] }[] = [];
    if (weakLinks.length > 0)
      items.push({
        label: `Rewrite ${weakLinks.length} weak anchor text link(s) (e.g. "click here") with descriptive text.`,
        affected: asAffected(weakLinks, (l) => `anchor: "${l.anchor_text || ""}"`),
      });
    if (gaps.length > 0)
      items.push({
        label: `Add rel="noopener noreferrer" to ${gaps.length} link(s) opening in a new tab.`,
        affected: asAffected(gaps, (l) => `target="_blank" · ${l.anchor_text || "no anchor"}`),
      });
    if (orphan.length > 0)
      items.push({
        label: `Add internal links to ${orphan.length} orphan page(s) with zero inbound links.`,
        affected: orphan.slice(0, 200).map((url) => ({ value: url, detail: "0 inbound links" })),
      });
    return items;
  }, [weakLinks, gaps, orphan]);

  const homepageUrl = results[0]?.url;
  const [activeGap, setActiveGap] = useState<(LinkEntry & { __kind: "internal" | "external" }) | null>(null);

  const brokenInternal = internal.filter((l) => l.is_broken).length;
  const brokenExternal = external.filter((l) => l.is_broken).length;
  const nofollowExternal = external.filter((l) => l.is_nofollow).length;

  function goToTab(t: Tab, filter?: LinkFilterPreset) {
    setLinkFilter(filter || {});
    setTab(t);
  }

  const healthData = [
    { name: "OK", value: health.ok, key: "ok" },
    { name: "Broken", value: health.broken, key: "broken" },
    { name: "Redirect", value: health.redirect, key: "redirect" },
    { name: "Unknown", value: health.unknown, key: "unknown" },
  ].filter((d) => d.value > 0);

  const followData = [
    { name: "Dofollow", value: allLinks.filter((l) => l.is_dofollow).length },
    { name: "Nofollow", value: allLinks.filter((l) => l.is_nofollow).length },
  ].filter((d) => d.value > 0);

  return (
    <div>
      <TabBar tabs={TABS} active={tab} onChange={goToTab} />

      {tab === "Overview" ? (
        <div className="flex flex-col gap-4">
          <Card>
            <div className="mb-2 flex items-center justify-between">
              <h3 className="text-sm font-semibold text-[var(--seo-subheading)]">
                Executive Summary
              </h3>
              <span className="text-xs text-[var(--seo-muted)]" title="Computed deterministically from the audit data below, not an LLM-generated write-up.">
                Rule-based summary
              </span>
            </div>
            <div className="mb-3 flex items-center gap-3">
              <span
                className="text-3xl font-bold"
                style={{ color: summary.linkHealthScore >= 90 ? "var(--seo-success)" : summary.linkHealthScore >= 70 ? "var(--seo-accent)" : summary.linkHealthScore >= 50 ? "var(--seo-warning)" : "var(--seo-error)" }}
              >
                {summary.linkHealthScore}
              </span>
              <span className="text-sm text-[var(--seo-text-light)]">Link Health Score</span>
            </div>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div>
                <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-[var(--seo-muted)]">
                  Top Priority Fixes
                </h4>
                {topPriorityItems.length ? (
                  <div className="flex flex-col gap-3">
                    {topPriorityItems.map((f, i) => (
                      <div key={i}>
                        <p className="text-sm text-[var(--seo-text)]">{f.label}</p>
                        <AffectedList affected={f.affected} />
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-sm text-[var(--seo-muted)]">No broken or redirecting links found.</p>
                )}
              </div>
              <div>
                <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-[var(--seo-muted)]">
                  Quick Wins
                </h4>
                {quickWinItems.length ? (
                  <div className="flex flex-col gap-3">
                    {quickWinItems.map((f, i) => (
                      <div key={i}>
                        <p className="text-sm text-[var(--seo-text)]">{f.label}</p>
                        <AffectedList affected={f.affected} />
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-sm text-[var(--seo-muted)]">Nothing quick to fix, nice work.</p>
                )}
              </div>
            </div>
          </Card>

          <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
            <div style={cardTint(HEALTH_COLORS.ok)}>
              <MetricCard label="Internal Links" value={internal.length} onClick={() => goToTab("Links", { type: "internal" })} />
            </div>
            <div style={cardTint(HEALTH_COLORS.ok)}>
              <MetricCard label="External Links" value={external.length} onClick={() => goToTab("Links", { type: "external" })} />
            </div>
            <div style={cardTint(HEALTH_COLORS.broken)}>
              <MetricCard
                label="Broken Internal"
                value={brokenInternal}
                onClick={() => goToTab("Links", { type: "internal", health: "broken" })}
              />
            </div>
            <div style={cardTint(HEALTH_COLORS.broken)}>
              <MetricCard
                label="Broken External"
                value={brokenExternal}
                onClick={() => goToTab("Links", { type: "external", health: "broken" })}
              />
            </div>
            <div style={cardTint(HEALTH_COLORS.redirect)}>
              <MetricCard
                label="Nofollow External"
                value={nofollowExternal}
                onClick={() => goToTab("Links", { type: "external", follow: "nofollow" })}
              />
            </div>
            <div style={cardTint(HEALTH_COLORS.broken)}>
              <MetricCard label="Orphan Pages" value={orphan.length} onClick={() => goToTab("Opportunities")} />
            </div>
            <div style={cardTint(HEALTH_COLORS.unknown)}>
              <MetricCard label="Special Links" value={specialLinks.length} onClick={() => goToTab("Special Links")} />
            </div>
            <div style={cardTint(STATUS_COLOR_HEX.warning)}>
              <MetricCard label="Security Gaps" value={gaps.length} onClick={() => goToTab("Opportunities")} />
            </div>
            <div style={cardTint(HEALTH_COLORS.ok)}>
              <MetricCard
                label="Body Content Links"
                value={allLinks.filter((l) => (l.location || "body") === "body").length}
                onClick={() => goToTab("Links", { location: "body" })}
              />
            </div>
          </div>

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <Card>
              <h3 className="mb-3 text-sm font-semibold text-[var(--seo-subheading)]">Link Health</h3>
              {healthData.length ? (
                <ResponsiveContainer width="100%" height={220}>
                  <PieChart>
                    <Pie
                      data={healthData}
                      dataKey="value"
                      nameKey="name"
                      outerRadius={80}
                      fill="#8884d8"
                      isAnimationActive={false}
                      label
                    >
                      {healthData.map((d, i) => (
                        <Cell key={i} fill={HEALTH_COLORS[d.key]} />
                      ))}
                    </Pie>
                    <Tooltip />
                    <Legend />
                  </PieChart>
                </ResponsiveContainer>
              ) : null}
            </Card>
            <Card>
              <h3 className="mb-3 text-sm font-semibold text-[var(--seo-subheading)]">Dofollow vs Nofollow</h3>
              {followData.length ? (
                <ResponsiveContainer width="100%" height={220}>
                  <PieChart>
                    <Pie
                      data={followData}
                      dataKey="value"
                      nameKey="name"
                      outerRadius={80}
                      fill="#8884d8"
                      isAnimationActive={false}
                      label
                    >
                      {followData.map((_, i) => (
                        <Cell key={i} fill={FOLLOW_COLORS[i % FOLLOW_COLORS.length]} />
                      ))}
                    </Pie>
                    <Tooltip />
                    <Legend />
                  </PieChart>
                </ResponsiveContainer>
              ) : null}
            </Card>
          </div>

          <Card className="overflow-x-auto p-0">
            <h3 className="px-4 pt-4 text-sm font-semibold text-[var(--seo-subheading)]">
              Top External Domains
            </h3>
            <table className="mt-2 w-full text-sm">
              <thead>
                <tr className="border-b border-[var(--seo-border)] bg-[var(--table-header-bg)] text-left text-xs uppercase tracking-wide text-[var(--seo-muted)]">
                  <th className="px-4 py-3">Domain</th>
                  <th className="px-4 py-3">Category</th>
                  <th className="px-4 py-3">Links</th>
                  <th className="px-4 py-3">Dofollow</th>
                  <th className="px-4 py-3">Broken</th>
                </tr>
              </thead>
              <tbody>
                {domainStats.map((d) => (
                  <tr key={d.domain} className="border-b border-[var(--table-row-border)]">
                    <td className="px-4 py-3 font-medium text-[var(--seo-subheading)]">{d.domain}</td>
                    <td className="px-4 py-3">
                      <span className="rounded-full bg-[var(--seo-accent-light)] px-2 py-0.5 text-xs font-medium text-[var(--seo-accent)]">
                        {d.category}
                      </span>
                    </td>
                    <td className="px-4 py-3">{d.count}</td>
                    <td className="px-4 py-3">{d.dofollow}</td>
                    <td className="px-4 py-3">{d.broken > 0 ? <span className="text-[var(--seo-error)]">{d.broken}</span> : 0}</td>
                  </tr>
                ))}
                {domainStats.length === 0 ? (
                  <tr>
                    <td colSpan={5} className="px-4 py-6 text-center text-[var(--seo-muted)]">
                      No external links found.
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </Card>
        </div>
      ) : null}

      {tab === "Links" ? (
        <LinkTable
          links={allLinks}
          showSource={results.length > 1}
          initialFilter={linkFilter}
          homepageUrl={homepageUrl}
          focusValue={focusValue}
          focusSeq={focusSeq}
        />
      ) : null}

      {tab === "Special Links" ? <SpecialLinksTable links={specialLinks} showSource={results.length > 1} /> : null}

      {tab === "Anchor Text" ? (
        <div className="flex flex-col gap-4">
          <Card>
            <h3 className="mb-2 text-sm font-semibold text-[var(--seo-subheading)]">
              Duplicate Anchors ({dupAnchors.length})
            </h3>
            <p className="mb-2 text-xs text-[var(--seo-text-light)]">
              Same anchor text used for links pointing to different destinations: confusing for users
              and dilutes the relevance signal each destination gets from that phrase.
            </p>
            <div className="flex flex-col gap-2">
              {dupAnchors.slice(0, 15).map((d, i) => (
                <div key={i} className="border-b border-[var(--seo-border)] pb-2 text-sm last:border-0">
                  <span className="font-medium text-[var(--seo-subheading)]">&quot;{d.anchor}&quot;</span>
                  <span className="ml-2 text-xs text-[var(--seo-muted)]">→ {d.destinations.length} different destinations</span>
                </div>
              ))}
              {dupAnchors.length === 0 ? <p className="text-sm text-[var(--seo-muted)]">None found.</p> : null}
            </div>
          </Card>
          <Card className="overflow-x-auto p-0">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-[var(--seo-border)] bg-[var(--table-header-bg)] text-left text-xs uppercase tracking-wide text-[var(--seo-muted)]">
                  <th className="px-4 py-3">Anchor Text</th>
                  <th className="px-4 py-3">Count</th>
                  <th className="px-4 py-3">% of Links</th>
                  <th className="px-4 py-3" />
                </tr>
              </thead>
              <tbody>
                {anchorDist.map((a, i) => (
                  <tr key={i} className="border-b border-[var(--table-row-border)]">
                    <td className="px-4 py-3">{a.anchor}</td>
                    <td className="px-4 py-3">{a.count}</td>
                    <td className="px-4 py-3">{a.pct}%</td>
                    <td className="px-4 py-3">
                      {a.isWeak ? (
                        <span className="rounded-full bg-[var(--seo-warning-bg)] px-2 py-0.5 text-xs font-medium text-[var(--seo-warning)]">
                          Weak
                        </span>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        </div>
      ) : null}

      {tab === "Opportunities" ? (
        <div className="flex flex-col gap-4">
          <Card>
            <h3 className="mb-2 text-sm font-semibold text-[var(--seo-subheading)]">
              Orphan Pages ({orphan.length})
            </h3>
            <p className="mb-2 text-xs text-[var(--seo-text-light)]">
              Audited pages with zero inbound internal links from other audited pages.
            </p>
            <ul className="list-inside list-disc text-sm text-[var(--seo-text)]">
              {orphan.map((url) => (
                <li key={url} className="truncate">{url}</li>
              ))}
              {orphan.length === 0 ? <li className="text-[var(--seo-muted)]">None found.</li> : null}
            </ul>
          </Card>
          <Card>
            <h3 className="mb-2 text-sm font-semibold text-[var(--seo-subheading)]">
              Low Internal Links ({lowLink.length})
            </h3>
            <p className="mb-2 text-xs text-[var(--seo-text-light)]">
              Pages with fewer than 3 inbound internal links.
            </p>
            <ul className="list-inside list-disc text-sm text-[var(--seo-text)]">
              {lowLink.map((url) => (
                <li key={url} className="truncate">{url}</li>
              ))}
              {lowLink.length === 0 ? <li className="text-[var(--seo-muted)]">None found.</li> : null}
            </ul>
          </Card>
          <Card>
            <h3 className="mb-2 text-sm font-semibold text-[var(--seo-subheading)]">
              Missing Security Attributes ({gaps.length})
            </h3>
            <p className="mb-2 text-xs text-[var(--seo-text-light)]">
              Links opening in a new tab without <code>rel=&quot;noopener noreferrer&quot;</code>:
              a tabnabbing / performance risk.
            </p>
            <ul className="text-sm text-[var(--seo-text)]">
              {gaps.slice(0, 25).map((l, i) => (
                <li
                  key={i}
                  role="button"
                  tabIndex={0}
                  onClick={() => setActiveGap(l)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") setActiveGap(l);
                  }}
                  style={{ backgroundColor: `${STATUS_COLOR_HEX.warning}18`, borderLeft: `3px solid ${STATUS_COLOR_HEX.warning}` }}
                  className="mb-1 cursor-pointer truncate rounded px-2 py-1.5 transition-shadow hover:shadow-sm"
                >
                  {l.url} <span className="text-xs text-[var(--seo-muted)]">({l.anchor_text})</span>
                  <span className="ml-2 text-xs font-medium text-[var(--seo-accent)]">View details & fix →</span>
                </li>
              ))}
              {gaps.length === 0 ? <li className="text-[var(--seo-muted)]">None found.</li> : null}
            </ul>
          </Card>
        </div>
      ) : null}

      <Modal
        open={!!activeGap}
        onClose={() => setActiveGap(null)}
        title={activeGap ? explainLink(activeGap, activeGap.__kind).issueName : undefined}
      >
        {activeGap ? <IssueDetail explanation={explainLink(activeGap, activeGap.__kind)} link={activeGap} /> : null}
      </Modal>
    </div>
  );
}

function LinkTable({
  links,
  showSource,
  initialFilter,
  homepageUrl,
  focusValue,
  focusSeq,
}: {
  links: (LinkEntry & { __kind: "internal" | "external" })[];
  showSource: boolean;
  initialFilter: LinkFilterPreset;
  homepageUrl?: string;
  focusValue?: string;
  focusSeq?: number;
}) {
  const [search, setSearch] = useState("");
  const [typeFilter, setTypeFilter] = useState<TypeFilter>(initialFilter.type || "all");
  const [followFilter, setFollowFilter] = useState<FollowFilter>(initialFilter.follow || "all");
  const [healthFilter, setHealthFilter] = useState<HealthFilter>(initialFilter.health || "all");
  const [categoryFilter, setCategoryFilter] = useState<CategoryFilter>("all");
  const [locationFilter, setLocationFilter] = useState<LocationFilter>(initialFilter.location || "all");
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [showDetails, setShowDetails] = useState(false);
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: "priority", dir: -1 });
  const [page, setPage] = useState(0);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [activeLink, setActiveLink] = useState<
    (LinkEntry & { __kind: "internal" | "external"; __priority: number }) | null
  >(null);

  // Jump-to-element target: setting search to the focused URL guarantees the
  // matching row lands on the first page (search is a plain URL substring
  // filter), then we scroll to + highlight it. setState/DOM reads run inside
  // timers (not the effect body) to satisfy react-hooks/set-state-in-effect and
  // to let the filtered table re-render before we query for the row.
  const containerRef = useRef<HTMLDivElement>(null);
  const lastFocusSeq = useRef<number | undefined>(undefined);
  useEffect(() => {
    if (focusSeq == null || !focusValue) return;
    if (lastFocusSeq.current === focusSeq) return;
    lastFocusSeq.current = focusSeq;
    const t = setTimeout(() => {
      setSearch(focusValue);
      setTypeFilter("all");
      setFollowFilter("all");
      setHealthFilter("all");
      setCategoryFilter("all");
      setLocationFilter("all");
      setStatusFilter("all");
      setPage(0);
      setSelected(new Set());
      // Let React re-render the filtered rows, then reveal the match.
      setTimeout(() => locateAndHighlight(containerRef.current, focusValue), 90);
    }, 0);
    return () => clearTimeout(t);
  }, [focusSeq, focusValue]);

  const statusCodes = useMemo(() => {
    const codes = new Set<number>();
    for (const l of links) if (l.status_code != null) codes.add(l.status_code);
    return [...codes].sort((a, b) => a - b);
  }, [links]);

  const withPriority = useMemo(
    () =>
      links.map((l) => ({
        ...l,
        __priority: priorityScore(l, l.__kind, homepageUrl ? l.sourceUrl === homepageUrl : false),
      })),
    [links, homepageUrl],
  );

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return withPriority.filter((l) => {
      if (q && !l.url.toLowerCase().includes(q) && !(l.anchor_text || "").toLowerCase().includes(q)) return false;
      if (typeFilter !== "all" && l.__kind !== typeFilter) return false;
      if (followFilter === "dofollow" && !l.is_dofollow) return false;
      if (followFilter === "nofollow" && !l.is_nofollow) return false;
      if (healthFilter === "broken" && !l.is_broken) return false;
      if (healthFilter === "redirect" && !l.is_redirect) return false;
      if (healthFilter === "ok" && (l.is_broken || l.is_redirect)) return false;
      if (categoryFilter !== "all" && (l.link_category || "page") !== categoryFilter) return false;
      if (locationFilter !== "all" && (l.location || "body") !== locationFilter) return false;
      if (statusFilter !== "all" && String(l.status_code) !== statusFilter) return false;
      return true;
    });
  }, [withPriority, search, typeFilter, followFilter, healthFilter, categoryFilter, locationFilter, statusFilter]);

  const sorted = useMemo(() => {
    const arr = [...filtered];
    arr.sort((a, b) => {
      let av: number | string = 0;
      let bv: number | string = 0;
      if (sort.key === "priority") {
        av = a.__priority;
        bv = b.__priority;
      } else if (sort.key === "response_time_ms") {
        av = a.response_time_ms ?? -1;
        bv = b.response_time_ms ?? -1;
      } else if (sort.key === "url") {
        av = a.url;
        bv = b.url;
      } else if (sort.key === "health") {
        av = a.health || "";
        bv = b.health || "";
      }
      if (av < bv) return -sort.dir;
      if (av > bv) return sort.dir;
      return 0;
    });
    return arr;
  }, [filtered, sort]);

  const pageCount = Math.max(1, Math.ceil(sorted.length / PAGE_SIZE));
  const pageSafe = Math.min(page, pageCount - 1);
  const pageLinks = sorted.slice(pageSafe * PAGE_SIZE, pageSafe * PAGE_SIZE + PAGE_SIZE);

  function toggleSort(key: SortKey) {
    setSort((s) => (s.key === key ? { key, dir: s.dir === 1 ? -1 : 1 } : { key, dir: -1 }));
    setSelected(new Set());
  }

  // "Select all" acts only on the rows currently rendered (this page, after the
  // active filter + sort), never on hidden rows the user can't see to uncheck.
  const allPageSelected = pageLinks.length > 0 && pageLinks.every((l) => selected.has(linkKey(l)));

  function toggleSelectAll() {
    if (allPageSelected) {
      setSelected(new Set());
    } else {
      setSelected(new Set(pageLinks.map(linkKey)));
    }
  }

  function toggleSelect(key: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  const selectedLinks = pageLinks.filter((l) => selected.has(linkKey(l)));

  function linksToCsvRows(subset: typeof sorted) {
    const rows: string[][] = [
      ["URL", "Source Page", "Anchor Text", "Type", "Follow", "Health", "Priority", "Status Code", "Response Time (ms)"],
    ];
    for (const l of subset) {
      rows.push([
        l.url,
        l.sourceUrl,
        l.anchor_text,
        l.__kind,
        l.is_dofollow ? "Dofollow" : "Nofollow",
        l.health || "unknown",
        String(l.__priority),
        String(l.status_code ?? ""),
        String(l.response_time_ms ?? ""),
      ]);
    }
    return rows;
  }

  function exportSelected() {
    downloadCsv("links-selected.csv", linksToCsvRows(selectedLinks));
  }

  function downloadCurrentView() {
    downloadCsv("links-filtered-view.csv", linksToCsvRows(sorted));
  }

  function copySelectedUrls() {
    const text = selectedLinks.map((l) => l.url).join("\n");
    navigator.clipboard?.writeText(text);
  }

  function openSelected() {
    selectedLinks.slice(0, 10).forEach((l) => window.open(l.url, "_blank", "noopener,noreferrer"));
  }

  return (
    <div className="flex flex-col gap-3" ref={containerRef}>
      <Card>
        <div className="flex flex-wrap items-center gap-3">
          <input
            type="text"
            placeholder="Search URL or anchor text…"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setPage(0);
              setSelected(new Set());
            }}
            className="min-w-[220px] flex-1 rounded-lg border border-[var(--seo-border-strong)] bg-[var(--seo-card-bg)] px-3 py-1.5 text-sm text-[var(--seo-text)]"
          />
          <select
            value={typeFilter}
            onChange={(e) => {
              setTypeFilter(e.target.value as TypeFilter);
              setPage(0);
              setSelected(new Set());
            }}
            className="rounded-lg border border-[var(--seo-border-strong)] bg-[var(--seo-card-bg)] px-3 py-1.5 text-sm text-[var(--seo-text)]"
          >
            <option value="all">Internal + External</option>
            <option value="internal">Internal only</option>
            <option value="external">External only</option>
          </select>
          <select
            value={followFilter}
            onChange={(e) => {
              setFollowFilter(e.target.value as FollowFilter);
              setPage(0);
              setSelected(new Set());
            }}
            className="rounded-lg border border-[var(--seo-border-strong)] bg-[var(--seo-card-bg)] px-3 py-1.5 text-sm text-[var(--seo-text)]"
          >
            <option value="all">All follow types</option>
            <option value="dofollow">Dofollow only</option>
            <option value="nofollow">Nofollow only</option>
          </select>
          <select
            value={healthFilter}
            onChange={(e) => {
              setHealthFilter(e.target.value as HealthFilter);
              setPage(0);
              setSelected(new Set());
            }}
            className="rounded-lg border border-[var(--seo-border-strong)] bg-[var(--seo-card-bg)] px-3 py-1.5 text-sm text-[var(--seo-text)]"
          >
            <option value="all">All health</option>
            <option value="ok">OK only</option>
            <option value="broken">Broken only</option>
            <option value="redirect">Redirects only</option>
          </select>
          <select
            value={categoryFilter}
            onChange={(e) => {
              setCategoryFilter(e.target.value as CategoryFilter);
              setPage(0);
              setSelected(new Set());
            }}
            className="rounded-lg border border-[var(--seo-border-strong)] bg-[var(--seo-card-bg)] px-3 py-1.5 text-sm text-[var(--seo-text)]"
          >
            <option value="all">All link types</option>
            <option value="page">Page</option>
            <option value="pdf">PDF</option>
            <option value="download">Download</option>
            <option value="image">Image</option>
          </select>
          <select
            value={locationFilter}
            onChange={(e) => {
              setLocationFilter(e.target.value as LocationFilter);
              setPage(0);
              setSelected(new Set());
            }}
            className="rounded-lg border border-[var(--seo-border-strong)] bg-[var(--seo-card-bg)] px-3 py-1.5 text-sm text-[var(--seo-text)]"
          >
            <option value="all">All locations</option>
            <option value="body">Body content</option>
            <option value="nav">Navigation</option>
            <option value="header">Header</option>
            <option value="footer">Footer</option>
            <option value="sidebar">Sidebar</option>
            <option value="breadcrumb">Breadcrumb</option>
          </select>
          {statusCodes.length > 0 ? (
            <select
              value={statusFilter}
              onChange={(e) => {
                setStatusFilter(e.target.value);
                setPage(0);
                setSelected(new Set());
              }}
              className="rounded-lg border border-[var(--seo-border-strong)] bg-[var(--seo-card-bg)] px-3 py-1.5 text-sm text-[var(--seo-text)]"
            >
              <option value="all">All HTTP status</option>
              {statusCodes.map((c) => (
                <option key={c} value={String(c)}>
                  {c}
                </option>
              ))}
            </select>
          ) : null}
          <label className="flex items-center gap-1.5 text-xs text-[var(--seo-text-light)]">
            <input type="checkbox" checked={showDetails} onChange={(e) => setShowDetails(e.target.checked)} />
            Show technical details
          </label>
          <span className="text-xs text-[var(--seo-muted)]">{sorted.length} link(s)</span>
          <button
            onClick={downloadCurrentView}
            className="ml-auto rounded-lg btn-gradient px-3 py-1.5 text-xs font-semibold text-white"
          >
            Download This View ({sorted.length})
          </button>
        </div>
      </Card>

      {selected.size > 0 ? (
        <Card className="flex flex-wrap items-center gap-2 py-2">
          <span className="text-xs font-medium text-[var(--seo-text-light)]">{selected.size} selected</span>
          <button onClick={exportSelected} className="rounded border border-[var(--seo-border-strong)] px-2 py-1 text-xs hover:bg-[var(--seo-card-hover)]">
            Export Selected
          </button>
          <button onClick={copySelectedUrls} className="rounded border border-[var(--seo-border-strong)] px-2 py-1 text-xs hover:bg-[var(--seo-card-hover)]">
            Copy URLs
          </button>
          <button onClick={openSelected} className="rounded border border-[var(--seo-border-strong)] px-2 py-1 text-xs hover:bg-[var(--seo-card-hover)]">
            Open (max 10)
          </button>
        </Card>
      ) : null}

      <Card className="overflow-x-auto p-0">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-[var(--seo-border)] bg-[var(--table-header-bg)] text-left text-xs uppercase tracking-wide text-[var(--seo-muted)]">
              <th className="px-3 py-3">
                <input
                  type="checkbox"
                  checked={allPageSelected}
                  onChange={toggleSelectAll}
                />
              </th>
              <th className="cursor-pointer px-4 py-3" onClick={() => toggleSort("url")}>
                URL {sort.key === "url" ? (sort.dir === 1 ? "▲" : "▼") : ""}
              </th>
              <th className="px-4 py-3">Type</th>
              {showSource ? <th className="px-4 py-3">Source Page</th> : null}
              <th className="px-4 py-3">Anchor Text</th>
              <th className="px-4 py-3">Follow</th>
              <th className="cursor-pointer px-4 py-3" onClick={() => toggleSort("health")}>
                Health {sort.key === "health" ? (sort.dir === 1 ? "▲" : "▼") : ""}
              </th>
              <th className="cursor-pointer px-4 py-3" onClick={() => toggleSort("priority")}>
                Priority {sort.key === "priority" ? (sort.dir === 1 ? "▲" : "▼") : ""}
              </th>
              {showDetails ? (
                <>
                  <th className="px-4 py-3">Category</th>
                  <th className="px-4 py-3">Location</th>
                  <th className="cursor-pointer px-4 py-3" onClick={() => toggleSort("response_time_ms")}>
                    Response {sort.key === "response_time_ms" ? (sort.dir === 1 ? "▲" : "▼") : ""}
                  </th>
                  <th className="px-4 py-3">Certainty</th>
                </>
              ) : null}
              <th className="px-4 py-3">Flags</th>
              <th className="px-4 py-3" />
            </tr>
          </thead>
          <tbody>
            {pageLinks.map((l, i) => {
              const secGap = l.opens_new_tab && (!l.has_noopener || !l.has_noreferrer);
              const rowColor = healthColorFor(l);
              const key = linkKey(l);
              return (
                <tr
                  key={i}
                  data-locate-value={l.url}
                  onClick={() => setActiveLink(l)}
                  className="cursor-pointer border-b border-[var(--table-row-border)] transition-shadow hover:shadow-sm"
                  style={{ backgroundColor: `${rowColor}0d`, borderLeft: `3px solid ${rowColor}` }}
                >
                  <td className="px-3 py-3" onClick={(e) => e.stopPropagation()}>
                    <input type="checkbox" checked={selected.has(key)} onChange={() => toggleSelect(key)} />
                  </td>
                  <td className="max-w-xs truncate px-4 py-3 text-[var(--seo-subheading)]">{l.url}</td>
                  <td className="px-4 py-3 capitalize">{l.__kind}</td>
                  {showSource ? (
                    <td className="max-w-[10rem] truncate px-4 py-3 text-xs text-[var(--seo-text-light)]">
                      {l.sourceUrl}
                    </td>
                  ) : null}
                  <td className="max-w-xs truncate px-4 py-3 text-[var(--seo-text-light)]">{l.anchor_text}</td>
                  <td className="px-4 py-3">{l.is_dofollow ? "Dofollow" : "Nofollow"}</td>
                  <td className="px-4 py-3 capitalize">
                    <span style={{ color: l.is_broken ? "var(--seo-error)" : "var(--seo-success)" }}>
                      {l.health || (l.is_broken ? "broken" : "ok")}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    {l.__priority > 0 ? (
                      <span
                        className="rounded-full px-2 py-0.5 text-xs font-semibold"
                        style={{
                          color: l.__priority >= 70 ? "var(--seo-error)" : "var(--seo-warning)",
                          backgroundColor: l.__priority >= 70 ? "var(--seo-error-bg)" : "var(--seo-warning-bg)",
                        }}
                      >
                        {l.__priority}
                      </span>
                    ) : (
                      <span className="text-[var(--seo-muted)]">N/A</span>
                    )}
                  </td>
                  {showDetails ? (
                    <>
                      <td className="px-4 py-3 capitalize">{l.link_category || "page"}</td>
                      <td className="px-4 py-3 capitalize">{l.location || "body"}</td>
                      <td className="px-4 py-3">{l.response_time_ms != null ? `${l.response_time_ms} ms` : "N/A"}</td>
                      <td className="px-4 py-3 text-xs">{linkCertainty(l)}</td>
                    </>
                  ) : null}
                  <td className="px-4 py-3">
                    <div className="flex gap-1">
                      {l.is_weak_anchor ? (
                        <span className="rounded-full bg-[var(--seo-warning-bg)] px-2 py-0.5 text-xs font-medium text-[var(--seo-warning)]">
                          Weak
                        </span>
                      ) : null}
                      {secGap ? (
                        <span className="rounded-full bg-[var(--seo-error-bg)] px-2 py-0.5 text-xs font-medium text-[var(--seo-error)]">
                          No noopener
                        </span>
                      ) : null}
                      {l.missing_target ? (
                        <span className="rounded-full bg-[var(--seo-card-hover)] px-2 py-0.5 text-xs font-medium text-[var(--seo-muted)]">
                          No target
                        </span>
                      ) : null}
                    </div>
                  </td>
                  <td className="px-4 py-3" onClick={(e) => e.stopPropagation()}>
                    <button
                      onClick={() => setActiveLink(l)}
                      className="text-xs font-medium text-[var(--seo-accent)] hover:underline"
                    >
                      Details
                    </button>
                  </td>
                </tr>
              );
            })}
            {pageLinks.length === 0 ? (
              <tr>
                <td colSpan={20} className="px-4 py-6 text-center text-[var(--seo-muted)]">
                  No links match this filter.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
        {pageCount > 1 ? (
          <div className="flex items-center justify-between border-t border-[var(--seo-border)] px-4 py-2 text-xs text-[var(--seo-text-light)]">
            <span>
              Page {pageSafe + 1} of {pageCount}
            </span>
            <div className="flex gap-2">
              <button
                type="button"
                disabled={pageSafe === 0}
                onClick={() => {
                  setPage((p) => Math.max(0, p - 1));
                  setSelected(new Set());
                }}
                className="rounded border border-[var(--seo-border-strong)] px-2 py-1 disabled:opacity-40"
              >
                Prev
              </button>
              <button
                type="button"
                disabled={pageSafe >= pageCount - 1}
                onClick={() => {
                  setPage((p) => Math.min(pageCount - 1, p + 1));
                  setSelected(new Set());
                }}
                className="rounded border border-[var(--seo-border-strong)] px-2 py-1 disabled:opacity-40"
              >
                Next
              </button>
            </div>
          </div>
        ) : null}
      </Card>

      <Modal
        open={!!activeLink}
        onClose={() => setActiveLink(null)}
        title={activeLink ? explainLink(activeLink, activeLink.__kind).issueName : undefined}
      >
        {activeLink ? <IssueDetail explanation={explainLink(activeLink, activeLink.__kind)} link={activeLink} /> : null}
      </Modal>
    </div>
  );
}

function IssueDetail({
  explanation,
  link,
}: {
  explanation: ReturnType<typeof explainLink>;
  link: LinkEntry;
}) {
  const color = STATUS_COLOR_HEX[explanation.status];
  return (
    <IssueExplanationGrid
      header={{ issueName: explanation.issueName, severity: explanation.severity, color }}
      fields={[
        { label: "What is it?", value: explanation.whatIsIt },
        { label: "Why is it important?", value: explanation.whyImportant },
        { label: "Root Cause", value: explanation.rootCause },
        {
          label: "Technical Details",
          value: (
            <>
              Status {link.status_code ?? "N/A"} · {link.response_time_ms != null ? `${link.response_time_ms} ms` : "not timed"}
              {link.redirect_path && link.redirect_path.length > 1 ? (
                <>
                  <br />
                  Redirect path: {link.redirect_path.join(" → ")}
                </>
              ) : null}
            </>
          ),
        },
        { label: "SEO Impact", value: explanation.seoImpact },
        { label: "User Impact", value: explanation.userImpact },
      ]}
      recommendedFix={explanation.recommendedFix}
      htmlExample={explanation.htmlExample}
    />
  );
}

function SpecialLinksTable({ links, showSource }: { links: SpecialLinkEntry[]; showSource: boolean }) {
  const [kindFilter, setKindFilter] = useState<"all" | SpecialLinkEntry["kind"]>("all");

  const filtered = useMemo(
    () => links.filter((l) => kindFilter === "all" || l.kind === kindFilter),
    [links, kindFilter],
  );

  const counts = useMemo(() => {
    const c: Record<string, number> = { mailto: 0, tel: 0, anchor: 0, javascript: 0 };
    for (const l of links) c[l.kind] = (c[l.kind] || 0) + 1;
    return c;
  }, [links]);

  return (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <MetricCard label="Mailto Links" value={counts.mailto} onClick={() => setKindFilter("mailto")} />
        <MetricCard label="Tel Links" value={counts.tel} onClick={() => setKindFilter("tel")} />
        <MetricCard label="Anchor Links" value={counts.anchor} onClick={() => setKindFilter("anchor")} />
        <MetricCard label="JS Links" value={counts.javascript} onClick={() => setKindFilter("javascript")} />
      </div>
      <Card>
        <select
          value={kindFilter}
          onChange={(e) => setKindFilter(e.target.value as typeof kindFilter)}
          className="rounded-lg border border-[var(--seo-border-strong)] bg-[var(--seo-card-bg)] px-3 py-1.5 text-sm text-[var(--seo-text)]"
        >
          <option value="all">All kinds</option>
          <option value="mailto">Mailto</option>
          <option value="tel">Tel</option>
          <option value="anchor">Anchor (#)</option>
          <option value="javascript">JavaScript</option>
        </select>
      </Card>
      <Card className="overflow-x-auto p-0">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-[var(--seo-border)] bg-[var(--table-header-bg)] text-left text-xs uppercase tracking-wide text-[var(--seo-muted)]">
              <th className="px-4 py-3">Href</th>
              <th className="px-4 py-3">Anchor Text</th>
              <th className="px-4 py-3">Kind</th>
              <th className="px-4 py-3">Location</th>
              {showSource ? <th className="px-4 py-3">Source Page</th> : null}
            </tr>
          </thead>
          <tbody>
            {filtered.slice(0, 300).map((l, i) => (
              <tr key={i} className="border-b border-[var(--table-row-border)]">
                <td className="max-w-xs truncate px-4 py-3 text-[var(--seo-subheading)]">{l.href}</td>
                <td className="max-w-xs truncate px-4 py-3 text-[var(--seo-text-light)]">{l.anchor_text}</td>
                <td className="px-4 py-3 capitalize">{l.kind}</td>
                <td className="px-4 py-3 capitalize">{l.location}</td>
                {showSource ? (
                  <td className="max-w-[10rem] truncate px-4 py-3 text-xs text-[var(--seo-text-light)]">{l.sourceUrl}</td>
                ) : null}
              </tr>
            ))}
            {filtered.length === 0 ? (
              <tr>
                <td colSpan={showSource ? 5 : 4} className="px-4 py-6 text-center text-[var(--seo-muted)]">
                  No special links found.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
