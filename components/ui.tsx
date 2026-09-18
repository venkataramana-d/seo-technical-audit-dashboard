import { useState, type CSSProperties, type ReactNode } from "react";
import { scoreColor, severityColor } from "@/lib/format";
import type { Issue } from "@/lib/types";
import { fixDifficulty, type Difficulty } from "@/lib/difficulty";
import { explainCommonIssue, type CommonIssueExplanation } from "@/lib/commonIssuesKB";
import { detectFixTarget, type FixPageContext, type FixSuggestion } from "@/lib/fixSuggestable";
import { SparklesIcon, XIcon } from "@/components/icons";

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`card p-5 ${className}`}>{children}</div>;
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
            <h3 className="text-base font-semibold tracking-tight text-[var(--seo-heading)]">{title}</h3>
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

/**
 * `pageContext`/`groqApiKey` are optional: only the Detail page (which has a
 * single concrete AuditResult in scope) passes them, enabling the
 * "✨ Suggest a fix" action inside the Modal for issues detectFixTarget()
 * recognizes (metadata/H1 issues with a well-defined, draftable
 * replacement). Callers without page context (e.g. any future
 * sitewide/aggregated issue list) just don't render it — there's no single
 * page to draft a fix for.
 */
export function IssueRow({
  issue,
  pageContext,
  groqApiKey,
}: {
  issue: Issue;
  pageContext?: FixPageContext;
  groqApiKey?: string;
}) {
  const [open, setOpen] = useState(false);
  const explanation = explainCommonIssue(issue);
  const fixTarget = pageContext ? detectFixTarget(issue.issue) : null;

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="block w-full border-b border-[var(--seo-border)] py-3 text-left transition-colors last:border-0 hover:bg-[var(--seo-card-hover)]"
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
      <Modal open={open} onClose={() => setOpen(false)} title={issue.issue}>
        <div className="flex flex-col gap-4">
          <CommonIssueDetail explanation={explanation} />
          {fixTarget && pageContext ? (
            <FixSuggestionButton issue={issue} pageContext={pageContext} apiKey={groqApiKey} />
          ) : null}
        </div>
      </Modal>
    </>
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
 * PerformanceView (ImageIssueDetail), and LinksView (IssueDetail) — the copy
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
          <div key={i}>
            <h5 className="text-xs font-semibold uppercase tracking-wide text-[var(--seo-muted)]">{f.label}</h5>
            <p className="text-[var(--seo-text)]">{f.value}</p>
          </div>
        ))}
      </div>
      <div>
        <h5 className="text-xs font-semibold uppercase tracking-wide text-[var(--seo-muted)]">Recommended Fix</h5>
        <p className="text-[var(--seo-text)]">{recommendedFix}</p>
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
