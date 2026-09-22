import { useState, type CSSProperties, type ReactNode } from "react";
import { scoreColor, severityColor } from "@/lib/format";
import type { AffectedElement, Issue } from "@/lib/types";
import { fixDifficulty, type Difficulty } from "@/lib/difficulty";
import { explainCommonIssue, type CommonIssueExplanation } from "@/lib/commonIssuesKB";
import { detectFixTarget, type FixPageContext, type FixSuggestion } from "@/lib/fixSuggestable";
import { EyeIcon, EyeOffIcon, SparklesIcon, XIcon } from "@/components/icons";

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`card p-5 ${className}`}>{children}</div>;
}

/**
 * Password field with a show/hide toggle. Single source of truth for the
 * password inputs across login, reset, and change-password (previously
 * copy-pasted). `className` styles the <input> exactly like a plain input; the
 * reveal button is overlaid on the right and the input gets right padding so
 * text never sits under it.
 */
export function PasswordInput({
  id,
  value,
  onChange,
  placeholder,
  autoComplete,
  minLength,
  required,
  className = "",
}: {
  id?: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  autoComplete?: string;
  minLength?: number;
  required?: boolean;
  className?: string;
}) {
  const [show, setShow] = useState(false);
  return (
    <div className="relative">
      <input
        id={id}
        type={show ? "text" : "password"}
        className={`${className} pr-10`}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        autoComplete={autoComplete}
        minLength={minLength}
        required={required}
      />
      <button
        type="button"
        onClick={() => setShow((s) => !s)}
        aria-label={show ? "Hide password" : "Show password"}
        aria-pressed={show}
        tabIndex={-1}
        className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-[var(--seo-muted)] transition hover:text-[var(--seo-text)]"
      >
        {show ? <EyeOffIcon size={17} /> : <EyeIcon size={17} />}
      </button>
    </div>
  );
}

/** Shared popup/modal: click-to-expand result cards across Issues, Links,
 * Headings, Performance, and Recommendations all open the same overlay
 * instead of each tab reinventing its own expand pattern. */
export function Modal({
  open,
  onClose,
  title,
  children,
}: {
  open: boolean;
  onClose: () => void;
  title?: string;
  children: ReactNode;
}) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <button
        type="button"
        aria-label="Close"
        className="absolute inset-0 bg-black/50"
        onClick={onClose}
      />
      <div
        role="dialog"
        aria-modal="true"
        className="card relative z-10 max-h-[85vh] w-full max-w-2xl overflow-y-auto p-5"
        style={{ borderRadius: "var(--seo-radius-lg)", boxShadow: "var(--seo-shadow-lg)" }}
      >
        <div className="mb-3 flex items-start justify-between gap-3">
          {title ? (
            <h3 className="min-w-0 break-words [overflow-wrap:anywhere] text-base font-semibold tracking-tight text-[var(--seo-heading)]">{title}</h3>
          ) : (
            <span />
          )}
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="shrink-0 rounded-lg p-1 text-[var(--seo-muted)] transition-colors hover:bg-[var(--seo-card-hover)] hover:text-[var(--seo-heading)]"
          >
            <XIcon size={16} />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

/** Shared tab-bar row (was copy-pasted byte-for-byte across LinksView,
 * HeadingsView, PerformanceView's Mobile/Image SEO switch, and the Detail
 * page's 8 top-level tabs). `T` is whatever string union the caller's tab
 * state uses. */
export function TabBar<T extends string>({
  tabs,
  active,
  onChange,
}: {
  tabs: readonly T[];
  active: T;
  onChange: (tab: T) => void;
}) {
  return (
    <div className="mb-4 flex flex-wrap gap-0.5 rounded-lg border border-[var(--seo-border)] bg-[var(--seo-card-bg-alt)] p-1">
      {tabs.map((t) => (
        <button
          key={t}
          onClick={() => onChange(t)}
          className={`rounded-md px-3 py-1.5 text-[13px] font-medium transition-colors ${
            active === t
              ? "bg-[var(--seo-card-bg)] text-[var(--seo-heading)] shadow-sm"
              : "text-[var(--seo-text-light)] hover:text-[var(--seo-subheading)]"
          }`}
        >
          {t}
        </button>
      ))}
    </div>
  );
}

export function MetricCard({
  label,
  value,
  sub,
  onClick,
}: {
  label: string;
  value: ReactNode;
  sub?: string;
  onClick?: () => void;
}) {
  return (
    <Card
      className={onClick ? "cursor-pointer transition-colors hover:border-[var(--seo-border-strong)]" : ""}
    >
      <button
        type="button"
        onClick={onClick}
        disabled={!onClick}
        className="w-full text-left disabled:cursor-default"
      >
        <div className="text-[11px] font-semibold uppercase tracking-wider text-[var(--seo-muted)]">
          {label}
        </div>
        <div className="mt-1.5 text-[26px] font-semibold leading-none tracking-tight text-[var(--seo-heading)] tabular-nums">{value}</div>
        {sub ? <div className="mt-1.5 text-xs text-[var(--seo-text-light)]">{sub}</div> : null}
      </button>
    </Card>
  );
}

export function ScoreBadge({ score }: { score: number }) {
  const color = scoreColor(score);
  return (
    <span
      className="pill"
      style={{ color, backgroundColor: `${color}18` }}
    >
      {Math.round(score)}
    </span>
  );
}

export function SeverityBadge({ severity }: { severity: string }) {
  const { text, bg } = severityColor(severity);
  return (
    <span className="pill capitalize" style={{ color: text, backgroundColor: bg }}>
      {severity}
    </span>
  );
}

/** CSS conic-gradient score circle, replaces flat score badges on results/overview pages. */
export function ScoreCircle({
  score,
  size = 72,
  label,
}: {
  score: number;
  size?: number;
  label?: string;
}) {
  const color = scoreColor(score);
  const deg = `${Math.max(0, Math.min(100, score)) * 3.6}deg`;
  return (
    <div className="flex flex-col items-center gap-1">
      <div
        className="score-circle"
        style={
          {
            "--score-size": `${size}px`,
            "--score-color": color,
            "--score-deg": deg,
          } as CSSProperties
        }
      >
        <span>{Math.round(score)}</span>
      </div>
      {label ? <span className="text-xs text-[var(--seo-muted)]">{label}</span> : null}
    </div>
  );
}

const DIFFICULTY_STYLE: Record<Difficulty, { color: string; bg: string }> = {
  Easy: { color: "var(--seo-success)", bg: "var(--seo-success-bg)" },
  Medium: { color: "var(--seo-warning)", bg: "var(--seo-warning-bg)" },
  Hard: { color: "var(--seo-error)", bg: "var(--seo-error-bg)" },
};

/** "Effort to fix" pill (Easy / Medium / Hard), derived from an issue's effort. */
export function DifficultyBadge({ difficulty }: { difficulty: Difficulty }) {
  const s = DIFFICULTY_STYLE[difficulty];
  return (
    <span className="pill" style={{ color: s.color, backgroundColor: s.bg }} title="Estimated effort to fix">
      {difficulty} fix
    </span>
  );
}

// --- "Where is the issue?" helpers -------------------------------------------
// The backend attaches Issue.affected - the exact offending elements (image
// src, link target, title/H1 text, canonical URL, redirect hop, etc.). These
// helpers decide when an affected value is a URL/image worth linkifying, and
// how to resolve a relative value against the audited page's URL.

const IMAGE_EXT_RE = /\.(png|jpe?g|gif|webp|avif|svg|bmp|ico|tiff?)(?:[?#].*)?$/i;

/** True when the value clearly denotes a URL (absolute http(s) or root-relative). */
export function looksLikeUrl(value: string): boolean {
  const v = (value || "").trim();
  return /^https?:\/\//i.test(v) || v.startsWith("/");
}

/** True when the value points at an image file (by extension). */
export function looksLikeImage(value: string): boolean {
  return IMAGE_EXT_RE.test((value || "").trim());
}

/** Resolve an affected value into an openable href, or null when it can't be
 * safely linked. Relative values resolve against the audited page (baseUrl) so
 * a new-tab open goes to the real resource, not the dashboard's own origin. */
export function affectedHref(value: string, baseUrl?: string): string | null {
  const v = (value || "").trim();
  if (/^https?:\/\//i.test(v)) return v;
  if (baseUrl) {
    try {
      return new URL(v, baseUrl).href;
    } catch {
      /* fall through */
    }
  }
  if (v.startsWith("/")) return v;
  return null;
}

/**
 * Find the row/element tagged with `data-locate-value === value` inside
 * `container`, scroll it into view and flash a temporary highlight ring (~2s).
 * Returns whether a match was found. Used by the Detail page's jump-to-element:
 * clicking an affected element switches to the relevant tab and calls this to
 * reveal the exact matching row. `fuzzy` also matches when one value contains
 * the other (image srcs can be stored relative in one place, absolute in
 * another). The highlight is applied via inline style (no global CSS needed).
 */
export function locateAndHighlight(
  container: HTMLElement | null,
  value: string,
  opts?: { fuzzy?: boolean },
): boolean {
  if (!container || !value) return false;
  const rows = Array.from(container.querySelectorAll<HTMLElement>("[data-locate-value]"));
  let match: HTMLElement | null = null;
  for (const el of rows) {
    if (el.getAttribute("data-locate-value") === value) {
      match = el;
      break;
    }
  }
  if (!match && opts?.fuzzy) {
    for (const el of rows) {
      const v = el.getAttribute("data-locate-value") || "";
      if (v && (v.includes(value) || value.includes(v))) {
        match = el;
        break;
      }
    }
  }
  if (!match) return false;
  match.scrollIntoView({ behavior: "smooth", block: "center" });
  const prev = match.style.boxShadow;
  const prevTransition = match.style.transition;
  match.style.transition = "box-shadow 0.3s ease";
  match.style.boxShadow = "0 0 0 2px var(--seo-card-bg), 0 0 0 4px var(--seo-accent)";
  window.setTimeout(() => {
    if (!match) return;
    match.style.boxShadow = prev;
    match.style.transition = prevTransition;
  }, 2000);
  return true;
}

/** Issue categories whose offending element has a home in a detail tab we can
 * jump to (Technical tab for canonical/robots/hreflang/schema/redirects/etc,
 * Links tab for links, Images tab for images). Used to decide when to show the
 * "Locate" jump even for non-URL values (e.g. a hreflang code, a schema type). */
const LOCATABLE_CATEGORY_RE =
  /link|image|canonical|indexab|international|structured data|redirect|technical|mobile|security|site health|social|metadata|heading/i;

export function isLocatableCategory(category?: string): boolean {
  return !!category && LOCATABLE_CATEGORY_RE.test(category);
}

/** One affected value, monospace + truncated (title = full). URL/image-like
 * values render as a new-tab link; a "Locate" affordance (when `onLocate` is
 * given) jumps to the matching row in the relevant detail tab. `locateCategory`
 * carries the issue's category so the jump routes to the right tab even when the
 * value isn't a URL. */
function AffectedValue({
  value,
  baseUrl,
  onLocate,
  locateCategory,
}: {
  value: string;
  baseUrl?: string;
  onLocate?: (value: string, category?: string) => void;
  locateCategory?: string;
}) {
  const linkable = looksLikeUrl(value) || looksLikeImage(value);
  const href = linkable ? affectedHref(value, baseUrl) : null;
  const canLocate = linkable || isLocatableCategory(locateCategory);
  return (
    <span className="flex min-w-0 flex-1 items-baseline gap-1.5">
      {href ? (
        <a
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          title={value}
          className="min-w-0 flex-1 truncate font-mono text-[var(--seo-accent)] hover:underline"
        >
          {value}
        </a>
      ) : (
        <span title={value} className="min-w-0 flex-1 truncate font-mono text-[var(--seo-text)]">
          {value}
        </span>
      )}
      {onLocate && canLocate ? (
        <button
          type="button"
          onClick={() => onLocate(value, locateCategory)}
          title="Find this element in the detailed table"
          className="shrink-0 rounded px-1 text-[10px] font-semibold uppercase tracking-wide text-[var(--seo-muted)] transition-colors hover:bg-[var(--seo-card-hover)] hover:text-[var(--seo-accent)]"
        >
          Locate
        </button>
      ) : null}
    </span>
  );
}

/**
 * Compact, scannable list of an issue's offending elements (Issue.affected):
 * exactly WHERE the issue is. Each row shows the value (monospace, truncated,
 * full text on hover) plus a muted detail. URL/image values become new-tab
 * links, and - when `onLocate` is supplied - expose a "Locate" jump. Long lists
 * cap at `cap` (~15) with a "+N more" expander so a page with hundreds of
 * offending elements stays readable.
 */
export function AffectedList({
  affected,
  baseUrl,
  onLocate,
  locateCategory,
  cap = 15,
}: {
  affected: AffectedElement[];
  baseUrl?: string;
  onLocate?: (value: string, category?: string) => void;
  locateCategory?: string;
  cap?: number;
}) {
  const [showAll, setShowAll] = useState(false);
  if (!affected || affected.length === 0) return null;
  const visible = showAll ? affected : affected.slice(0, cap);
  const hidden = affected.length - visible.length;
  return (
    <div className="mt-2 rounded-lg border border-[var(--seo-border)] bg-[var(--seo-card-bg-alt)] p-2">
      <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-[var(--seo-muted)]">
        Affected element{affected.length === 1 ? "" : "s"} ({affected.length})
      </div>
      <ul className="flex flex-col gap-1">
        {visible.map((a, i) => (
          <li key={`${a.value}-${i}`} className="flex items-baseline gap-2 text-xs leading-relaxed">
            <AffectedValue value={a.value} baseUrl={baseUrl} onLocate={onLocate} locateCategory={locateCategory} />
            {a.detail ? (
              <span className="shrink-0 text-[var(--seo-muted)]" title={a.detail}>
                {a.detail}
              </span>
            ) : null}
          </li>
        ))}
      </ul>
      {hidden > 0 ? (
        <button
          type="button"
          onClick={() => setShowAll(true)}
          className="mt-1 text-[11px] font-medium text-[var(--seo-accent)] hover:underline"
        >
          +{hidden} more
        </button>
      ) : null}
      {showAll && affected.length > cap ? (
        <button
          type="button"
          onClick={() => setShowAll(false)}
          className="mt-1 text-[11px] font-medium text-[var(--seo-muted)] hover:underline"
        >
          Show less
        </button>
      ) : null}
    </div>
  );
}

/**
 * `pageContext`/`groqApiKey` are optional: only the Detail page (which has a
 * single concrete AuditResult in scope) passes them, enabling the
 * "✨ Suggest a fix" action inside the Modal for issues detectFixTarget()
 * recognizes (metadata/H1 issues with a well-defined, draftable
 * replacement). Callers without page context (e.g. any future
 * sitewide/aggregated issue list) just don't render it - there's no single
 * page to draft a fix for.
 *
 * `onLocate` (Detail page only) wires the affected-element list's "Locate"
 * jump: clicking an offending element switches to the relevant detail tab and
 * scrolls to + highlights the matching row.
 */
export function IssueRow({
  issue,
  pageContext,
  groqApiKey,
  onLocate,
}: {
  issue: Issue;
  pageContext?: FixPageContext;
  groqApiKey?: string;
  onLocate?: (value: string, category?: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const explanation = explainCommonIssue(issue);
  const fixTarget = pageContext ? detectFixTarget(issue.issue) : null;
  const affected = issue.affected || [];

  return (
    <div className="border-b border-[var(--seo-border)] last:border-0">
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="block w-full pt-3 text-left transition-colors hover:bg-[var(--seo-card-hover)]"
      >
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <SeverityBadge severity={issue.severity} />
              <DifficultyBadge difficulty={fixDifficulty(issue)} />
              <span className="text-xs text-[var(--seo-muted)]">{issue.category}</span>
            </div>
            <div className="mt-1 text-sm font-medium text-[var(--seo-subheading)]">
              {issue.issue}
            </div>
            <div className="mt-0.5 text-xs text-[var(--seo-text-light)]">
              {issue.recommendation}
            </div>
            <span className="mt-1 inline-block text-xs font-medium text-[var(--seo-accent)]">
              View details & fix →
            </span>
          </div>
          <span className="shrink-0 text-xs text-[var(--seo-muted)]">
            Impact {issue.impact_score}
          </span>
        </div>
      </button>
      {affected.length > 0 ? (
        <div className="pb-3">
          <AffectedList affected={affected} baseUrl={pageContext?.url} onLocate={onLocate} locateCategory={issue.category} />
        </div>
      ) : (
        <div className="pb-3" />
      )}
      <Modal open={open} onClose={() => setOpen(false)} title={issue.issue}>
        <div className="flex flex-col gap-4">
          <CommonIssueDetail explanation={explanation} />
          {affected.length > 0 ? (
            <div>
              <h5 className="mb-1 text-xs font-semibold uppercase tracking-wide text-[var(--seo-muted)]">
                Where it occurs
              </h5>
              <AffectedList affected={affected} baseUrl={pageContext?.url} onLocate={onLocate} locateCategory={issue.category} />
            </div>
          ) : null}
          {fixTarget && pageContext ? (
            <FixSuggestionButton issue={issue} pageContext={pageContext} apiKey={groqApiKey} />
          ) : null}
        </div>
      </Modal>
    </div>
  );
}

function FixSuggestionButton({
  issue,
  pageContext,
  apiKey,
}: {
  issue: Issue;
  pageContext: FixPageContext;
  apiKey?: string;
}) {
  const [state, setState] = useState<"idle" | "loading">("idle");
  const [result, setResult] = useState<FixSuggestion | null>(null);
  const [copied, setCopied] = useState(false);

  async function run() {
    setState("loading");
    setResult(null);
    setCopied(false);
    try {
      const res = await fetch("/api/ai", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "fix-suggestion", issue: issue.issue, pageContext, apiKey: apiKey || undefined }),
      });
      const data: FixSuggestion = await res.json();
      setResult(data);
    } catch {
      setResult({ ok: false, error: "Request failed." });
    } finally {
      setState("idle");
    }
  }

  function copy() {
    if (!result?.suggestion) return;
    navigator.clipboard?.writeText(result.suggestion).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  }

  return (
    <div className="mt-1.5">
      <button
        type="button"
        onClick={run}
        disabled={state === "loading"}
        className="inline-flex items-center gap-1 text-xs font-medium text-[var(--seo-accent)] hover:underline disabled:opacity-60"
      >
        <SparklesIcon size={13} />
        {state === "loading" ? "Drafting…" : "Suggest a fix"}
      </button>
      {result?.ok ? (
        <div className="mt-2 rounded-lg bg-[var(--seo-card-hover)] p-2 text-xs">
          <p className="text-[var(--seo-text)]">{result.suggestion}</p>
          {result.rationale ? <p className="mt-1 text-[var(--seo-muted)]">{result.rationale}</p> : null}
          <button
            type="button"
            onClick={copy}
            className="mt-1 font-medium text-[var(--seo-accent)] hover:underline"
          >
            {copied ? "Copied!" : "Copy"}
          </button>
        </div>
      ) : null}
      {result && !result.ok ? (
        <p className="mt-1 text-xs text-[var(--seo-error)]">{result.error}</p>
      ) : null}
    </div>
  );
}

function CommonIssueDetail({ explanation }: { explanation: CommonIssueExplanation }) {
  return (
    <div className="mt-3 grid grid-cols-1 gap-3 rounded-lg bg-[var(--seo-card-hover)] p-3 sm:grid-cols-2">
      <div>
        <h5 className="text-xs font-semibold uppercase tracking-wide text-[var(--seo-muted)]">What is it?</h5>
        <p className="text-sm text-[var(--seo-text)]">{explanation.whatIsIt}</p>
      </div>
      <div>
        <h5 className="text-xs font-semibold uppercase tracking-wide text-[var(--seo-muted)]">Why it matters</h5>
        <p className="text-sm text-[var(--seo-text)]">{explanation.whyItMatters}</p>
      </div>
      <div>
        <h5 className="text-xs font-semibold uppercase tracking-wide text-[var(--seo-muted)]">SEO impact</h5>
        <p className="text-sm text-[var(--seo-text)]">{explanation.seoImpact}</p>
      </div>
      <div>
        <h5 className="text-xs font-semibold uppercase tracking-wide text-[var(--seo-muted)]">User impact</h5>
        <p className="text-sm text-[var(--seo-text)]">{explanation.userImpact}</p>
      </div>
      <div className="sm:col-span-2">
        <h5 className="text-xs font-semibold uppercase tracking-wide text-[var(--seo-muted)]">Recommended fix</h5>
        <p className="text-sm text-[var(--seo-text)]">{explanation.recommendedFix}</p>
        {explanation.source ? (
          <p className="mt-1 text-xs text-[var(--seo-muted)]">Source: {explanation.source}</p>
        ) : null}
      </div>
    </div>
  );
}

/** Shared "issue explanation" grid: What is it / Why it matters / SEO impact /
 * User impact / Recommended fix. Was independently re-typed in HeadingsView,
 * PerformanceView (ImageIssueDetail), and LinksView (IssueDetail) - the copy
 * had already drifted ("why it matters" vs "why is it important?") before
 * this was consolidated. `fields` lets callers insert extra cells (LinksView
 * adds Root Cause / Technical Details) while keeping one shared layout. */
export function IssueExplanationGrid({
  header,
  fields,
  recommendedFix,
  htmlExample,
}: {
  header?: { issueName: string; severity: string; color: string };
  fields: { label: string; value: ReactNode }[];
  recommendedFix: ReactNode;
  htmlExample?: string;
}) {
  return (
    <div className="flex flex-col gap-3 text-sm">
      {header ? (
        <div className="flex items-center gap-2">
          <span
            className="rounded-full px-2 py-0.5 text-xs font-semibold"
            style={{ color: header.color, backgroundColor: `${header.color}18` }}
          >
            {header.issueName}
          </span>
          <span className="text-xs text-[var(--seo-muted)]">Severity: {header.severity}</span>
        </div>
      ) : null}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {fields.map((f, i) => (
          // min-w-0 lets the grid track shrink; break-words + overflow-wrap:anywhere
          // wraps long unbroken strings (e.g. URL-encoded image filenames) so they
          // can't overflow and overlap the neighbouring column.
          <div key={i} className="min-w-0">
            <h5 className="text-xs font-semibold uppercase tracking-wide text-[var(--seo-muted)]">{f.label}</h5>
            <p className="break-words [overflow-wrap:anywhere] text-[var(--seo-text)]">{f.value}</p>
          </div>
        ))}
      </div>
      <div>
        <h5 className="text-xs font-semibold uppercase tracking-wide text-[var(--seo-muted)]">Recommended Fix</h5>
        <p className="break-words [overflow-wrap:anywhere] text-[var(--seo-text)]">{recommendedFix}</p>
        {htmlExample ? (
          <pre className="mt-1 overflow-x-auto rounded-lg bg-[var(--seo-card-hover)] p-2 text-xs text-[var(--seo-subheading)]">
            {htmlExample}
          </pre>
        ) : null}
      </div>
    </div>
  );
}

const CHECK_STATUS_STYLE: Record<string, { color: string; bg: string; label: string }> = {
  pass: { color: "var(--seo-success)", bg: "var(--seo-success-bg)", label: "Pass" },
  warning: { color: "var(--seo-warning)", bg: "var(--seo-warning-bg)", label: "Warning" },
  fail: { color: "var(--seo-error)", bg: "var(--seo-error-bg)", label: "Fail" },
  info: { color: "var(--seo-muted)", bg: "var(--seo-card-hover)", label: "Info" },
};

/** Pass/Warning/Fail pill, used by the Technical SEO Audit checklist view. */
export function StatusPill({ status }: { status: string }) {
  const s = CHECK_STATUS_STYLE[status] ?? CHECK_STATUS_STYLE.warning;
  return (
    <span className="pill" style={{ color: s.color, backgroundColor: s.bg }}>
      {s.label}
    </span>
  );
}

export function EmptyState({ title, hint }: { title: string; hint?: string }) {
  return (
    <Card className="flex flex-col items-center justify-center py-16 text-center">
      <div className="text-lg font-semibold text-[var(--seo-heading)]">{title}</div>
      {hint ? <div className="mt-1 text-sm text-[var(--seo-muted)]">{hint}</div> : null}
    </Card>
  );
}

/** Always-visible "how to use this" note, placed directly under the section
 * it explains. Replaces the old click-to-open HelpDialog popover pattern:
 * the explanation is part of the page, not hidden behind an (i) icon. */
export function HelpSection({ title, children }: { title?: string; children: ReactNode }) {
  return (
    <div className="mt-2 rounded-lg border border-[var(--seo-border)] bg-[var(--seo-card-bg-alt)] px-3 py-2">
      {title ? (
        <div className="mb-0.5 text-xs font-semibold uppercase tracking-wide text-[var(--seo-muted)]">
          How to use: {title}
        </div>
      ) : null}
      <p className="text-xs leading-relaxed text-[var(--seo-text-light)]">{children}</p>
    </div>
  );
}

export function PageHeader({
  title,
  subtitle,
  icon,
  actions,
}: {
  title: string;
  subtitle?: string;
  icon?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-wrap items-start justify-between gap-3">
      <div className="flex items-center gap-3">
        {icon ? (
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-[var(--seo-border)] bg-[var(--seo-card-bg)] text-[var(--seo-accent)]">
            {icon}
          </span>
        ) : null}
        <div>
          <h1 className="text-[22px] font-semibold tracking-tight text-[var(--seo-heading)]">{title}</h1>
          {subtitle ? <p className="mt-0.5 text-sm text-[var(--seo-text-light)]">{subtitle}</p> : null}
        </div>
      </div>
      {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
    </div>
  );
}
