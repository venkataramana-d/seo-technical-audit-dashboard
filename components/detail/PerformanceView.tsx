"use client";

import { useEffect, useMemo, useRef, useState, Fragment } from "react";
import { Card, IssueExplanationGrid, locateAndHighlight, MetricCard, Modal, TabBar } from "@/components/ui";
import { downloadCsv } from "@/lib/format";
import type { AuditResult } from "@/lib/types";
import {
  ALT_STATUS_LABEL,
  explainImageIssue,
  flattenImages,
  formatBreakdown,
  formatBytes,
  imagePriorityScore,
  STATUS_COLOR_HEX,
  type ImageEntry,
} from "@/lib/imageAnalysis";
import { Cell, Legend, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";

const CWV_KEYS = ["ttfb", "fcp", "lcp", "cls", "tbt", "si", "inp"] as const;

const CWV_INFO: Record<string, { label: string; good: string; needsWork: string }> = {
  ttfb: { label: "Time to First Byte", good: "< 200ms", needsWork: "200–500ms" },
  fcp: { label: "First Contentful Paint", good: "< 1.8s", needsWork: "1.8–3s" },
  lcp: { label: "Largest Contentful Paint", good: "< 2.5s", needsWork: "2.5–4s" },
  cls: { label: "Cumulative Layout Shift", good: "< 0.1", needsWork: "0.1–0.25" },
  tbt: { label: "Total Blocking Time", good: "< 200ms", needsWork: "200–600ms" },
  si: { label: "Speed Index", good: "< 3.4s", needsWork: "3.4–5.8s" },
  inp: { label: "Interaction to Next Paint", good: "< 200ms", needsWork: "200–500ms" },
};

const CWV_SUGGESTION: Record<string, string> = {
  ttfb: "Speed up your server response: enable caching, put the origin behind a CDN, and cut backend processing or database query latency.",
  fcp: "Eliminate render-blocking CSS/JS, inline critical styles, and minimize server response time so the browser can paint sooner.",
  lcp: "Optimize your largest image/text block: compress and preload the LCP resource, and remove render-blocking CSS/JS ahead of it.",
  cls: "Reserve space for images, ads, and embeds with explicit width/height, avoid injecting content above existing content, and preload web fonts to prevent layout jumps.",
  tbt: "Break up long JavaScript tasks, defer or remove unused JS, and limit third-party scripts that block the main thread.",
  si: "Speed up how quickly content becomes visible: reduce JS execution time, remove render-blocking resources, and streamline the critical rendering path.",
  inp: "Reduce input-handler latency: split long tasks, trim large JS bundles that block the main thread, and keep event callbacks fast.",
};

// Field (real-user CrUX) Core Web Vitals returned by modules/pagespeed.py as
// livePsi.field. Order follows Google's own CrUX priority; each entry may be
// absent from the payload when that metric has no field data.
const FIELD_METRICS: { key: string; label: string }[] = [
  { key: "lcp", label: "LCP" },
  { key: "inp", label: "INP" },
  { key: "cls", label: "CLS" },
  { key: "fcp", label: "FCP" },
  { key: "ttfb", label: "TTFB" },
];

const FIELD_STATUS_LABEL: Record<string, string> = {
  good: "Good",
  "needs-improvement": "Needs work",
  poor: "Poor",
};

interface MobileCheck {
  id: string;
  name: string;
  category: string;
  status: string;
  value: string;
  detail: string;
}

interface PsiOpportunity {
  id: string;
  title: string;
  description: string;
  displayValue: string;
  score: number | null;
}

type AltFilter = "all" | ImageEntry["alt_status"];
type FormatFilter = "all" | string;
type ImageSort = "priority" | "file_size_bytes" | "name";

function cwvColor(status: string) {
  if (status === "pass") return { text: "var(--cwv-good-text)", bg: "var(--cwv-good-bg)" };
  if (status === "warning") return { text: "var(--cwv-needs-text)", bg: "var(--cwv-needs-bg)" };
  if (status === "fail") return { text: "var(--cwv-poor-text)", bg: "var(--cwv-poor-bg)" };
  return { text: "var(--seo-muted)", bg: "var(--seo-card-hover)" };
}

/** Field (CrUX) status → color tokens. The backend emits "good" |
 * "needs-improvement" | "poor" (distinct from the lab pass/warning/fail that
 * cwvColor handles), so this maps them to the shared --seo-* status tokens. */
function fieldStatusColor(status: string) {
  if (status === "good") return { text: "var(--seo-success)", bg: "var(--seo-success-bg)" };
  if (status === "needs-improvement") return { text: "var(--seo-warning)", bg: "var(--seo-warning-bg)" };
  if (status === "poor") return { text: "var(--seo-error)", bg: "var(--seo-error-bg)" };
  return { text: "var(--seo-muted)", bg: "var(--seo-card-hover)" };
}

/** Format a p75 field percentile: CLS is unitless (2 decimals); the rest are
 * milliseconds, shown as seconds once they cross 1000ms to stay readable. */
function formatFieldValue(key: string, percentile: number) {
  if (key === "cls") return percentile.toFixed(2);
  if (percentile >= 1000) return `${(percentile / 1000).toFixed(2)} s`;
  return `${Math.round(percentile)} ms`;
}

const CATEGORY_ORDER = [
  "Mobile Basics", "Responsiveness", "Usability", "Readability", "Navigation",
  "User Experience", "Conversion", "Performance", "Layout", "Accessibility",
];

/** Tint a summary tile based on whether it's counting a real problem
 * (count > 0 = bad) or a clean result (count === 0 = good). Reuses the
 * same cwvColor status→color mapping the CWV tiles already use. */
function issueCountColors(count: number) {
  return cwvColor(count > 0 ? "fail" : "pass");
}

function groupChecksByCategory(checks: MobileCheck[]) {
  const groups = new Map<string, MobileCheck[]>();
  for (const c of checks) {
    const list = groups.get(c.category) || [];
    list.push(c);
    groups.set(c.category, list);
  }
  return CATEGORY_ORDER.filter((cat) => groups.has(cat)).map((cat) => ({ category: cat, checks: groups.get(cat)! }));
}

export function PerformanceView({
  result,
  focusValue,
  focusSeq,
}: {
  result: AuditResult;
  /** An image src the Detail page wants revealed (jump-to-element). */
  focusValue?: string;
  /** Bumps each time a jump is requested, re-triggering even for the same value. */
  focusSeq?: number;
}) {
  const [subTab, setSubTab] = useState<"Mobile" | "Image SEO">("Mobile");

  // Jump-to-element: an image src focus switches to the Image SEO sub-tab where
  // the per-image table lives; ImageSeoTab then scrolls to + highlights the row.
  // setState runs inside requestAnimationFrame, not the effect body, per the
  // repo's react-hooks/set-state-in-effect rule.
  const lastFocusSeq = useRef<number | undefined>(undefined);
  useEffect(() => {
    if (focusSeq == null || !focusValue) return;
    if (lastFocusSeq.current === focusSeq) return;
    lastFocusSeq.current = focusSeq;
    const raf = requestAnimationFrame(() => setSubTab("Image SEO"));
    return () => cancelAnimationFrame(raf);
  }, [focusSeq, focusValue]);
  const [psiLoading, setPsiLoading] = useState(false);
  const [psiError, setPsiError] = useState<string | null>(null);
  const [openCwvKey, setOpenCwvKey] = useState<string | null>(null);
  const [openCheck, setOpenCheck] = useState<MobileCheck | null>(null);
  const [openOpportunity, setOpenOpportunity] = useState<PsiOpportunity | null>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any -- PSI JSON is dynamically shaped; typed accessors below narrow at use sites.
  const [livePsi, setLivePsi] = useState<Record<string, any> | null>(null);

  // Reset cached PSI + fetch state whenever the target URL changes so a stale
  // fetch from a previously-viewed URL never leaks into this one.
  useEffect(() => {
    setLivePsi(null);
    setPsiError(null);
    setPsiLoading(false);
  }, [result.url]);

  const r = result;
  const mobile = r.mobile_audit || {};
  // /api/pagespeed (modules/pagespeed.py fetch_pagespeed) returns metrics as
  // flat top-level fields, not nested under a "cwv" key like mobile_audit.cwv does.
  const cwv = livePsi
    ? {
        ttfb: livePsi.ttfb,
        fcp: livePsi.fcp,
        lcp: livePsi.lcp,
        cls: livePsi.cls,
        tbt: livePsi.tbt,
        si: livePsi.si,
        inp: livePsi.inp,
        source: livePsi.source,
      }
    : mobile.cwv || {};
  const opportunities: PsiOpportunity[] = livePsi?.opportunities || [];

  const groupedChecks = groupChecksByCategory(mobile.checks || []);

  async function fetchLivePsi() {
    setPsiLoading(true);
    setPsiError(null);
    try {
      const res = await fetch("/api/audit-pipeline", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "pagespeed", url: r.url, strategy: "mobile" }),
      });
      const data = await res.json();
      if (!res.ok || data.success === false) {
        setPsiError(data.error || "PageSpeed fetch failed.");
        return;
      }
      setLivePsi(data);
    } catch (err) {
      setPsiError(err instanceof Error ? err.message : "PageSpeed fetch failed.");
    } finally {
      setPsiLoading(false);
    }
  }

  return (
    <div>
      <TabBar tabs={["Mobile", "Image SEO"] as const} active={subTab} onChange={setSubTab} />

      {subTab === "Mobile" ? (
        <div className="flex flex-col gap-4">
          <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
            <MetricCard label="Mobile Score" value={mobile.mobile_score ?? "N/A"} />
            <MetricCard label="Mobile Friendly" value={mobile.is_mobile_friendly ? "Yes" : "No"} />
            <MetricCard
              label="Checks Passed"
              value={`${mobile.passed_checks ?? 0}/${mobile.total_checks ?? 0}`}
            />
            <MetricCard label="CWV Source" value={cwv.source || "N/A"} />
          </div>

          {livePsi ? (
            livePsi.field ? (
              <Card>
                <div className="mb-1 flex items-center justify-between">
                  <h3 className="text-sm font-semibold text-[var(--seo-subheading)]">
                    Field Data (real users, CrUX)
                  </h3>
                  {livePsi.field_overall ? (
                    <span className="text-xs font-semibold uppercase text-[var(--seo-muted)]">
                      Overall: {livePsi.field_overall}
                    </span>
                  ) : null}
                </div>
                <p className="mb-3 text-xs text-[var(--seo-text-light)]">
                  p75 field data from the Chrome UX Report — what real Chrome users actually
                  experienced, and what Google ranks on. The lab Core Web Vitals below are
                  diagnostic (a single simulated load). Scope:{" "}
                  {livePsi.field.scope === "origin"
                    ? "origin (site-wide) fallback"
                    : "page-level"}
                  . INP replaced FID as a Core Web Vital in 2024.
                </p>
                <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
                  {FIELD_METRICS.map(({ key, label }) => {
                    const metric = livePsi.field[key];
                    if (!metric || metric.percentile == null) return null;
                    const colors = fieldStatusColor(metric.status);
                    return (
                      <div
                        key={key}
                        className="rounded-lg border border-[var(--seo-border)] p-3 text-center"
                      >
                        <div className="text-xs uppercase text-[var(--seo-muted)]">{label}</div>
                        <div className="mt-1 text-lg font-bold text-[var(--seo-text)]">
                          {formatFieldValue(key, metric.percentile)}
                        </div>
                        <span
                          className="mt-1.5 inline-block rounded-full px-2 py-0.5 text-[10px] font-semibold"
                          style={{ color: colors.text, backgroundColor: colors.bg }}
                        >
                          {FIELD_STATUS_LABEL[metric.status] || metric.status}
                        </span>
                      </div>
                    );
                  })}
                </div>
              </Card>
            ) : (
              <div className="rounded-lg border border-[var(--seo-border)] bg-[var(--seo-card-hover)] px-3 py-2 text-xs text-[var(--seo-muted)]">
                No field (CrUX) data — this page/origin doesn&apos;t have enough real-user
                traffic yet.
              </div>
            )
          ) : null}

          <Card>
            <div className="mb-3 flex items-center justify-between">
              <h3 className="text-sm font-semibold text-[var(--seo-subheading)]">
                Core Web Vitals
              </h3>
              <button
                type="button"
                onClick={fetchLivePsi}
                disabled={psiLoading}
                className="rounded-lg btn-gradient px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-60"
              >
                {psiLoading ? "Fetching…" : "Fetch Live PSI"}
              </button>
            </div>
            {psiError ? (
              <div className="mb-3 rounded-lg border border-[var(--seo-error-border)] bg-[var(--seo-error-bg)] px-3 py-2 text-xs text-[var(--seo-error)]">
                {psiError}
              </div>
            ) : null}
            {livePsi ? (
              <div className="mb-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
                <MetricCard label="Performance" value={livePsi.performance_score ?? "N/A"} />
                <MetricCard label="Accessibility" value={livePsi.accessibility_score ?? "N/A"} />
                <MetricCard label="SEO" value={livePsi.seo_score ?? "N/A"} />
                <MetricCard label="Best Practices" value={livePsi.best_practices_score ?? "N/A"} />
              </div>
            ) : null}
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              {CWV_KEYS.map((key) => {
                const metric = cwv[key];
                if (!metric) return null;
                const colors = cwvColor(metric.status);
                const info = CWV_INFO[key];
                return (
                  <button
                    key={key}
                    type="button"
                    onClick={() => setOpenCwvKey(key)}
                    className="rounded-lg p-3 text-center transition-shadow hover:shadow-md"
                    style={{ backgroundColor: colors.bg }}
                    title={info ? `${info.label} (good: ${info.good}, needs improvement: ${info.needsWork})` : undefined}
                  >
                    <div className="text-xs uppercase text-[var(--seo-muted)]">{key}</div>
                    <div className="mt-1 text-lg font-bold" style={{ color: colors.text }}>
                      {metric.value}
                    </div>
                  </button>
                );
              })}
            </div>
          </Card>

          <Modal
            open={openCwvKey !== null}
            onClose={() => setOpenCwvKey(null)}
            title={openCwvKey ? CWV_INFO[openCwvKey]?.label || openCwvKey : undefined}
          >
            {openCwvKey ? (() => {
              const metric = cwv[openCwvKey];
              const info = CWV_INFO[openCwvKey];
              const colors = cwvColor(metric?.status);
              return (
                <div className="flex flex-col gap-3 text-sm">
                  <div className="rounded-lg p-3" style={{ backgroundColor: colors.bg }}>
                    <div className="text-xs uppercase text-[var(--seo-muted)]">Current value</div>
                    <div className="mt-1 text-xl font-bold" style={{ color: colors.text }}>
                      {metric?.value ?? "N/A"}
                    </div>
                  </div>
                  {info ? (
                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <div className="text-xs font-semibold uppercase text-[var(--seo-muted)]">Good</div>
                        <div className="text-[var(--seo-text)]">{info.good}</div>
                      </div>
                      <div>
                        <div className="text-xs font-semibold uppercase text-[var(--seo-muted)]">Needs improvement</div>
                        <div className="text-[var(--seo-text)]">{info.needsWork}</div>
                      </div>
                    </div>
                  ) : null}
                  <div>
                    <div className="mb-1 text-xs font-semibold uppercase text-[var(--seo-muted)]">Suggested action</div>
                    <p className="text-[var(--seo-text)]">
                      {CWV_SUGGESTION[openCwvKey] || "Review this metric and address the underlying performance bottleneck."}
                    </p>
                  </div>
                </div>
              );
            })() : null}
          </Modal>

          {opportunities.length > 0 ? (
            <Card>
              <h3 className="mb-2 text-sm font-semibold text-[var(--seo-subheading)]">
                PageSpeed Opportunities
              </h3>
              <p className="mb-2 text-xs text-[var(--seo-text-light)]">
                Real Lighthouse findings from the live PSI fetch, ranked by potential impact.
              </p>
              <div className="flex flex-col gap-2">
                {opportunities.map((op, i) => (
                  <button
                    type="button"
                    key={i}
                    onClick={() => setOpenOpportunity(op)}
                    className="w-full border-b border-[var(--seo-border)] py-2 text-left last:border-0 hover:bg-[var(--seo-card-hover)]"
                  >
                    <div className="flex items-center justify-between text-sm">
                      <span className="font-medium text-[var(--seo-subheading)]">{op.title}</span>
                      {op.displayValue ? (
                        <span className="text-xs font-semibold text-[var(--seo-warning)]">{op.displayValue}</span>
                      ) : null}
                    </div>
                    {op.description ? (
                      <p className="mt-0.5 text-xs text-[var(--seo-text-light)]">{op.description}</p>
                    ) : null}
                  </button>
                ))}
              </div>
            </Card>
          ) : null}

          <Modal
            open={openOpportunity !== null}
            onClose={() => setOpenOpportunity(null)}
            title={openOpportunity?.title}
          >
            {openOpportunity ? (
              <div className="flex flex-col gap-3 text-sm">
                {openOpportunity.displayValue ? (
                  <div className="rounded-lg bg-[var(--seo-warning-bg)] p-3 text-center text-lg font-bold text-[var(--seo-warning)]">
                    {openOpportunity.displayValue}
                  </div>
                ) : null}
                {openOpportunity.description ? (
                  <p className="text-[var(--seo-text)]">{openOpportunity.description}</p>
                ) : (
                  <p className="text-[var(--seo-muted)]">No further description available.</p>
                )}
              </div>
            ) : null}
          </Modal>

          <Card>
            <h3 className="mb-2 text-sm font-semibold text-[var(--seo-subheading)]">
              Mobile Checks
            </h3>
            <div className="flex flex-col gap-3">
              {groupedChecks.map(({ category, checks }) => (
                <div key={category}>
                  <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-[var(--seo-muted)]">
                    {category}
                  </h4>
                  {checks.map((c, i) => (
                    <button
                      type="button"
                      key={i}
                      onClick={() => setOpenCheck(c)}
                      className="flex w-full items-center justify-between rounded-md border-b border-[var(--seo-border)] px-2 py-1.5 text-left text-sm last:border-0 hover:shadow-sm"
                      style={{ backgroundColor: cwvColor(c.status).bg }}
                    >
                      <span className="text-[var(--seo-text)]">{c.name}</span>
                      <span
                        className="text-xs font-medium capitalize"
                        style={{
                          color:
                            c.status === "pass"
                              ? "var(--seo-success)"
                              : c.status === "fail"
                              ? "var(--seo-error)"
                              : "var(--seo-muted)",
                        }}
                      >
                        {c.status} {c.value ? `: ${c.value}` : ""}
                      </span>
                    </button>
                  ))}
                </div>
              ))}
            </div>
          </Card>

          <Modal
            open={openCheck !== null}
            onClose={() => setOpenCheck(null)}
            title={openCheck?.name}
          >
            {openCheck ? (
              <div className="flex flex-col gap-3 text-sm">
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <div className="text-xs font-semibold uppercase text-[var(--seo-muted)]">Category</div>
                    <div className="text-[var(--seo-text)]">{openCheck.category}</div>
                  </div>
                  <div>
                    <div className="text-xs font-semibold uppercase text-[var(--seo-muted)]">Status</div>
                    <div
                      className="font-medium capitalize"
                      style={{
                        color:
                          openCheck.status === "pass"
                            ? "var(--seo-success)"
                            : openCheck.status === "fail"
                            ? "var(--seo-error)"
                            : "var(--seo-muted)",
                      }}
                    >
                      {openCheck.status}
                    </div>
                  </div>
                </div>
                {openCheck.value ? (
                  <div>
                    <div className="text-xs font-semibold uppercase text-[var(--seo-muted)]">Value</div>
                    <div className="text-[var(--seo-text)]">{openCheck.value}</div>
                  </div>
                ) : null}
                <div>
                  <div className="mb-1 text-xs font-semibold uppercase text-[var(--seo-muted)]">Details</div>
                  <p className="text-[var(--seo-text)]">
                    {openCheck.detail || `Review this check in context of ${openCheck.category}.`}
                  </p>
                </div>
              </div>
            ) : null}
          </Modal>
        </div>
      ) : (
        <ImageSeoTab results={[r]} showSource={false} focusValue={focusValue} focusSeq={focusSeq} />
      )}
    </div>
  );
}

export function ImageSeoTab({
  results,
  showSource,
  focusValue,
  focusSeq,
}: {
  results: AuditResult[];
  showSource: boolean;
  focusValue?: string;
  focusSeq?: number;
}) {
  const images = useMemo(() => flattenImages(results), [results]);
  const formats = useMemo(() => formatBreakdown(images), [images]);
  const [altFilter, setAltFilter] = useState<AltFilter>("all");
  const [formatFilter, setFormatFilter] = useState<FormatFilter>("all");
  const [lcpOnly, setLcpOnly] = useState(false);
  const [issueOnly, setIssueOnly] = useState(false);
  const [brokenOnly, setBrokenOnly] = useState(false);
  const [sort, setSort] = useState<{ key: ImageSort; dir: 1 | -1 }>({ key: "priority", dir: -1 });
  const [expanded, setExpanded] = useState<number | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());

  // Jump-to-element: clear the filters so the focused image isn't hidden, then
  // scroll to + highlight its row. Image srcs can be stored relative in one
  // place and absolute in another, so match fuzzily. setState/DOM reads run in
  // timers (not the effect body) per react-hooks/set-state-in-effect, and to let
  // the reset filters re-render the table before we query for the row.
  const containerRef = useRef<HTMLDivElement>(null);
  const lastFocusSeq = useRef<number | undefined>(undefined);
  useEffect(() => {
    if (focusSeq == null || !focusValue) return;
    if (lastFocusSeq.current === focusSeq) return;
    lastFocusSeq.current = focusSeq;
    const t = setTimeout(() => {
      setAltFilter("all");
      setFormatFilter("all");
      setLcpOnly(false);
      setIssueOnly(false);
      setBrokenOnly(false);
      setTimeout(() => locateAndHighlight(containerRef.current, focusValue, { fuzzy: true }), 90);
    }, 0);
    return () => clearTimeout(t);
  }, [focusSeq, focusValue]);

  const missingAlt = images.filter((i) => i.alt_status === "missing").length;
  const largeImages = images.filter((i) => i.issues.includes("Large file size (> 300KB)")).length;
  // Derived from the per-image issues list (not a raw !has_lazy check) so this
  // matches modules/image_auditor.py's own exclusion of the LCP image from
  // the lazy-loading complaint: that image correctly should NOT be lazy.
  const noLazy = images.filter((i) => i.issues.includes("Missing lazy loading")).length;
  const brokenImages = images.filter((i) => i.is_broken === true).length;
  const formatOpportunities = images.filter((i) => i.issues.includes("Could be converted to WebP/AVIF"));
  const duplicateAlt = (() => {
    const seen = new Map<string, number>();
    for (const img of images) {
      const alt = (img.alt_text || "").trim().toLowerCase();
      if (alt) seen.set(alt, (seen.get(alt) || 0) + 1);
    }
    return [...seen.values()].filter((c) => c > 1).length;
  })();

  const withPriority = useMemo(
    () => images.map((img) => ({ ...img, __priority: imagePriorityScore(img) })),
    [images],
  );

  const filtered = useMemo(() => {
    return withPriority.filter((img) => {
      if (altFilter !== "all" && img.alt_status !== altFilter) return false;
      if (formatFilter !== "all" && img.format_label !== formatFilter) return false;
      if (lcpOnly && !img.is_lcp_candidate) return false;
      if (issueOnly && img.issues.length === 0) return false;
      if (brokenOnly && img.is_broken !== true) return false;
      return true;
    });
  }, [withPriority, altFilter, formatFilter, lcpOnly, issueOnly, brokenOnly]);

  const sorted = useMemo(() => {
    const arr = [...filtered];
    arr.sort((a, b) => {
      let av: number | string = 0;
      let bv: number | string = 0;
      if (sort.key === "priority") {
        av = a.__priority;
        bv = b.__priority;
      } else if (sort.key === "file_size_bytes") {
        av = a.file_size_bytes ?? -1;
        bv = b.file_size_bytes ?? -1;
      } else {
        av = a.name;
        bv = b.name;
      }
      if (av < bv) return -sort.dir;
      if (av > bv) return sort.dir;
      return 0;
    });
    return arr;
  }, [filtered, sort]);

  function toggleSort(key: ImageSort) {
    setSort((s) => (s.key === key ? { key, dir: s.dir === 1 ? -1 : 1 } : { key, dir: -1 }));
  }

  function toggleSelect(i: number) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(i)) next.delete(i);
      else next.add(i);
      return next;
    });
  }

  function toggleSelectAll() {
    if (selected.size === sorted.length && sorted.length > 0) setSelected(new Set());
    else setSelected(new Set(sorted.map((_, i) => i)));
  }

  function downloadView() {
    const rows: string[][] = [
      ["Name", "Source Page", "Format", "Alt Status", "Alt Text", "Dimensions", "Lazy", "File Size", "Broken", "LCP Candidate", "Issues"],
    ];
    const subset = selected.size > 0 ? sorted.filter((_, i) => selected.has(i)) : sorted;
    for (const img of subset) {
      rows.push([
        img.name,
        img.sourceUrl,
        img.format_label,
        img.alt_status,
        img.alt_text || "",
        img.has_dimensions ? `${img.width}×${img.height}` : "N/A",
        img.has_lazy ? "Yes" : "No",
        formatBytes(img.file_size_bytes),
        img.is_broken ? `Yes (${img.status_code ?? img.fetch_error ?? "unreachable"})` : "No",
        img.is_lcp_candidate ? "Yes" : "No",
        img.issues.join("; "),
      ]);
    }
    downloadCsv("image-seo-audit.csv", rows);
  }

  return (
    <div className="flex flex-col gap-4" ref={containerRef}>
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <MetricCard label="Total Images" value={images.length} />
        <TintedMetricCard
          label="Missing Alt Text"
          value={missingAlt}
          tint={issueCountColors(missingAlt)}
          onClick={() => setAltFilter("missing")}
        />
        <TintedMetricCard
          label="Large Images (>300KB)"
          value={largeImages}
          tint={issueCountColors(largeImages)}
          onClick={() => setIssueOnly(true)}
        />
        <TintedMetricCard label="Missing Lazy Load" value={noLazy} tint={issueCountColors(noLazy)} />
        <TintedMetricCard label="Duplicate Alt Text" value={duplicateAlt} tint={issueCountColors(duplicateAlt)} />
        <TintedMetricCard
          label="Format Upgrade Candidates"
          value={formatOpportunities.length}
          tint={issueCountColors(formatOpportunities.length)}
        />
        <TintedMetricCard
          label="Broken Images"
          value={brokenImages}
          tint={issueCountColors(brokenImages)}
          onClick={() => setBrokenOnly(true)}
        />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card>
          <h3 className="mb-3 text-sm font-semibold text-[var(--seo-subheading)]">Format Breakdown</h3>
          {formats.length ? (
            <ResponsiveContainer width="100%" height={200}>
              <PieChart>
                <Pie data={formats} dataKey="count" nameKey="format" outerRadius={75} fill="#8884d8" isAnimationActive={false} label>
                  {formats.map((_, i) => (
                    <Cell key={i} fill={["#0369A1", "#0284C7", "#059669", "#D97706", "#7C3AED", "#DC2626"][i % 6]} />
                  ))}
                </Pie>
                <Tooltip />
                <Legend />
              </PieChart>
            </ResponsiveContainer>
          ) : (
            <p className="text-sm text-[var(--seo-muted)]">No images found.</p>
          )}
        </Card>
        <Card>
          <h3 className="mb-2 text-sm font-semibold text-[var(--seo-subheading)]">
            Legacy Format Opportunities ({formatOpportunities.length})
          </h3>
          <p className="mb-2 text-xs text-[var(--seo-text-light)]">
            JPEG/PNG images that would likely be smaller as WebP or AVIF with no visible quality loss.
          </p>
          <ul className="text-sm text-[var(--seo-text)]">
            {formatOpportunities.slice(0, 8).map((img, i) => (
              <li key={i} className="truncate border-b border-[var(--seo-border)] py-1 last:border-0">
                {img.name} <span className="text-xs text-[var(--seo-muted)]">({formatBytes(img.file_size_bytes)})</span>
              </li>
            ))}
            {formatOpportunities.length === 0 ? <li className="text-[var(--seo-muted)]">None found.</li> : null}
          </ul>
        </Card>
      </div>

      <Card>
        <div className="flex flex-wrap items-center gap-3">
          <select
            value={altFilter}
            onChange={(e) => setAltFilter(e.target.value as AltFilter)}
            className="rounded-lg border border-[var(--seo-border-strong)] bg-[var(--seo-card-bg)] px-3 py-1.5 text-sm text-[var(--seo-text)]"
          >
            <option value="all">All alt statuses</option>
            {Object.entries(ALT_STATUS_LABEL).map(([k, label]) => (
              <option key={k} value={k}>{label}</option>
            ))}
          </select>
          <select
            value={formatFilter}
            onChange={(e) => setFormatFilter(e.target.value)}
            className="rounded-lg border border-[var(--seo-border-strong)] bg-[var(--seo-card-bg)] px-3 py-1.5 text-sm text-[var(--seo-text)]"
          >
            <option value="all">All formats</option>
            {formats.map((f) => (
              <option key={f.format} value={f.format}>{f.format}</option>
            ))}
          </select>
          <label className="flex items-center gap-1.5 text-xs text-[var(--seo-text-light)]">
            <input type="checkbox" checked={lcpOnly} onChange={(e) => setLcpOnly(e.target.checked)} />
            LCP candidate only
          </label>
          <label className="flex items-center gap-1.5 text-xs text-[var(--seo-text-light)]">
            <input type="checkbox" checked={issueOnly} onChange={(e) => setIssueOnly(e.target.checked)} />
            Has issues only
          </label>
          <label className="flex items-center gap-1.5 text-xs text-[var(--seo-text-light)]">
            <input type="checkbox" checked={brokenOnly} onChange={(e) => setBrokenOnly(e.target.checked)} />
            Broken only
          </label>
          <span className="text-xs text-[var(--seo-muted)]">{sorted.length} image(s)</span>
          <button
            onClick={downloadView}
            className="ml-auto rounded-lg btn-gradient px-3 py-1.5 text-xs font-semibold text-white"
          >
            Download {selected.size > 0 ? `Selected (${selected.size})` : `This View (${sorted.length})`}
          </button>
        </div>
      </Card>

      <Card className="overflow-x-auto p-0">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-[var(--seo-border)] bg-[var(--table-header-bg)] text-left text-xs uppercase tracking-wide text-[var(--seo-muted)]">
              <th className="px-3 py-3">
                <input type="checkbox" checked={selected.size > 0 && selected.size === sorted.length} onChange={toggleSelectAll} />
              </th>
              <th className="px-4 py-3">Preview</th>
              <th className="cursor-pointer px-4 py-3" onClick={() => toggleSort("name")}>
                Name {sort.key === "name" ? (sort.dir === 1 ? "▲" : "▼") : ""}
              </th>
              {showSource ? <th className="px-4 py-3">Source Page</th> : null}
              <th className="px-4 py-3">Alt Status</th>
              <th className="px-4 py-3">Format</th>
              <th className="px-4 py-3">Dimensions</th>
              <th className="cursor-pointer px-4 py-3" onClick={() => toggleSort("file_size_bytes")}>
                Size {sort.key === "file_size_bytes" ? (sort.dir === 1 ? "▲" : "▼") : ""}
              </th>
              <th className="cursor-pointer px-4 py-3" onClick={() => toggleSort("priority")}>
                Priority {sort.key === "priority" ? (sort.dir === 1 ? "▲" : "▼") : ""}
              </th>
              <th className="px-4 py-3" />
            </tr>
          </thead>
          <tbody>
            {sorted.slice(0, 200).map((img, i) => {
              const isExpanded = expanded === i;
              const explanations = img.issues.map((iss) => explainImageIssue(iss, img)).filter(Boolean);
              return (
                <Fragment key={i}>
                  <tr className="border-b border-[var(--table-row-border)]" data-locate-value={img.url}>
                    <td className="px-3 py-3">
                      <input type="checkbox" checked={selected.has(i)} onChange={() => toggleSelect(i)} />
                    </td>
                    <td className="px-4 py-3">
                      {img.url ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img src={img.url} alt="" className="h-10 w-10 rounded object-cover" loading="lazy" onError={(e) => { (e.target as HTMLImageElement).style.visibility = "hidden"; }} />
                      ) : null}
                    </td>
                    <td className="max-w-[10rem] truncate px-4 py-3 text-[var(--seo-subheading)]">
                      {img.url ? (
                        <a
                          href={img.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          title={img.url}
                          onClick={(e) => e.stopPropagation()}
                          className="hover:underline"
                        >
                          {img.name || "(unnamed)"}
                        </a>
                      ) : (
                        <span title={img.url}>{img.name || "(unnamed)"}</span>
                      )}
                      {img.is_lcp_candidate ? (
                        <span className="ml-1 rounded-full bg-[var(--seo-accent-light)] px-1.5 py-0.5 text-[10px] font-semibold text-[var(--seo-accent)]">LCP</span>
                      ) : null}
                      {img.is_broken ? (
                        <span
                          className="ml-1 rounded-full px-1.5 py-0.5 text-[10px] font-semibold"
                          style={{ color: "var(--seo-error)", backgroundColor: "var(--seo-error-bg)" }}
                          title={img.fetch_error || (img.status_code ? `HTTP ${img.status_code}` : "Unreachable")}
                        >
                          Broken
                        </span>
                      ) : null}
                    </td>
                    {showSource ? (
                      <td className="max-w-[10rem] truncate px-4 py-3 text-xs text-[var(--seo-text-light)]">{img.sourceUrl}</td>
                    ) : null}
                    <td className="px-4 py-3">
                      <span
                        className="rounded-full px-2 py-0.5 text-xs font-medium"
                        style={{
                          color: img.alt_status === "ok" ? "var(--seo-success)" : img.alt_status === "missing" ? "var(--seo-error)" : "var(--seo-warning)",
                          backgroundColor: img.alt_status === "ok" ? "var(--seo-success-bg)" : img.alt_status === "missing" ? "var(--seo-error-bg)" : "var(--seo-warning-bg)",
                        }}
                      >
                        {ALT_STATUS_LABEL[img.alt_status]}
                      </span>
                    </td>
                    <td className="px-4 py-3">{img.format_label}</td>
                    <td className="px-4 py-3">{img.has_dimensions ? `${img.width}×${img.height}` : "N/A"}</td>
                    <td className="px-4 py-3">{formatBytes(img.file_size_bytes)}</td>
                    <td className="px-4 py-3">
                      {img.__priority > 0 ? (
                        <span
                          className="rounded-full px-2 py-0.5 text-xs font-semibold"
                          style={{
                            color: img.__priority >= 60 ? "var(--seo-error)" : "var(--seo-warning)",
                            backgroundColor: img.__priority >= 60 ? "var(--seo-error-bg)" : "var(--seo-warning-bg)",
                          }}
                        >
                          {img.__priority}
                        </span>
                      ) : (
                        <span className="text-[var(--seo-muted)]">N/A</span>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      {explanations.length > 0 ? (
                        <button
                          onClick={() => setExpanded(isExpanded ? null : i)}
                          className="text-xs font-medium text-[var(--seo-accent)] hover:underline"
                        >
                          {isExpanded ? "Hide" : `Details (${explanations.length})`}
                        </button>
                      ) : (
                        <span className="text-xs text-[var(--seo-muted)]">No issues</span>
                      )}
                    </td>
                  </tr>
                </Fragment>
              );
            })}
            {sorted.length === 0 ? (
              <tr>
                <td colSpan={10} className="px-4 py-6 text-center text-[var(--seo-muted)]">
                  No images match this filter.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </Card>

      <Modal
        open={expanded !== null}
        onClose={() => setExpanded(null)}
        title={expanded !== null ? sorted[expanded]?.name || "(unnamed)" : undefined}
      >
        {expanded !== null && sorted[expanded] ? (
          <div className="flex flex-col gap-4">
            {sorted[expanded].issues
              .map((iss) => explainImageIssue(iss, sorted[expanded]))
              .filter(Boolean)
              .map((exp, idx) => exp && <ImageIssueDetail key={idx} explanation={exp} />)}
          </div>
        ) : null}
      </Modal>
    </div>
  );
}

/** Same layout as the shared MetricCard, but with a status-tinted background.
 * MetricCard itself has no color prop, so this reimplements its markup
 * locally (using the same global `.card` class) rather than touching
 * components/ui.tsx, which is off-limits for this change. */
function TintedMetricCard({
  label,
  value,
  tint,
  onClick,
}: {
  label: string;
  value: number;
  tint: { text: string; bg: string };
  onClick?: () => void;
}) {
  return (
    <div
      className={`card p-5 ${onClick ? "cursor-pointer transition-shadow hover:shadow-md" : ""}`}
      style={{ backgroundColor: tint.bg }}
    >
      <button
        type="button"
        onClick={onClick}
        disabled={!onClick}
        className="w-full text-left disabled:cursor-default"
      >
        <div className="text-xs font-medium uppercase tracking-wide text-[var(--seo-muted)]">{label}</div>
        <div className="mt-1 text-2xl font-bold" style={{ color: tint.text }}>
          {value}
        </div>
      </button>
    </div>
  );
}

function ImageIssueDetail({ explanation }: { explanation: ReturnType<typeof explainImageIssue> }) {
  if (!explanation) return null;
  const color = STATUS_COLOR_HEX[explanation.status];
  return (
    <IssueExplanationGrid
      header={{ issueName: explanation.issueName, severity: explanation.severity, color }}
      fields={[
        { label: "What is it?", value: explanation.whatIsIt },
        { label: "Why is it important?", value: explanation.whyImportant },
        { label: "SEO Impact", value: explanation.seoImpact },
        { label: "User Impact", value: explanation.userImpact },
      ]}
      recommendedFix={explanation.recommendedFix}
      htmlExample={explanation.htmlExample}
    />
  );
}
