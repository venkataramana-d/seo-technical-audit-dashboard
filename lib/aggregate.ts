import type { AuditResult, Issue } from "@/lib/types";

// Mirrors modules/scoring.py THEMES / get_thematic_issues / get_top_issues_by_impact:
// these are pure grouping/sorting functions over already-computed issue lists,
// so there's no need to round-trip to the Python API to recompute them.
// Substring match (see getThematicIssues): keywords must be substrings of the
// EXACT category strings modules/*.py emit. Kept in sync with scoring.py THEMES
// (see the note there: "Heading" not "Headings", the mobile-UX categories, and
// "Security" were all missing and fell through to "Other").
export const THEMES: Record<string, string[]> = {
  Crawlability: ["Accessibility", "Redirects", "Indexability", "URL Structure"],
  Metadata: ["Metadata"],
  // Page-Specific MUST precede Content: "Content" is a substring of "Course
  // Content"/"Blog Content", and the classifier breaks on first match, so
  // Content-first would misfile page-specific issues. Kept in sync with
  // modules/scoring.py THEMES.
  "Page-Specific": ["Course Content", "Blog Content", "Conversion"],
  Content: ["Content", "Heading", "Readability"],
  Links: ["Internal Links", "External Links"],
  Technical: [
    "Canonical",
    "Technical",
    "Mobile",
    "Performance",
    "Responsiveness",
    "Usability",
    "Navigation",
    "User Experience",
    "Layout",
  ],
  "Social & Schema": ["Structured Data", "Social SEO", "International SEO"],
  Images: ["Images", "Image SEO"],
  "Site Health": ["Site Health", "Security"],
};

export function getThematicIssues(allIssues: Issue[]): Record<string, Issue[]> {
  const grouped: Record<string, Issue[]> = {};
  for (const theme of Object.keys(THEMES)) grouped[theme] = [];
  const other: Issue[] = [];

  for (const issue of allIssues) {
    const cat = (issue.category || "").toLowerCase();
    let placed = false;
    for (const [theme, categories] of Object.entries(THEMES)) {
      if (categories.some((c) => cat.includes(c.toLowerCase()))) {
        grouped[theme].push(issue);
        placed = true;
        break;
      }
    }
    if (!placed) other.push(issue);
  }
  if (other.length) grouped["Other"] = other;

  return Object.fromEntries(Object.entries(grouped).filter(([, v]) => v.length > 0));
}

export function getTopIssuesByImpact(allIssues: Issue[], topN = 10): Issue[] {
  return [...allIssues]
    .sort((a, b) => (b.impact_score || 0) - (a.impact_score || 0))
    .slice(0, topN);
}

export function severityCounts(allIssues: Issue[]): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const issue of allIssues) {
    const sev = issue.severity || "Low";
    counts[sev] = (counts[sev] || 0) + 1;
  }
  return counts;
}

export function scoreDistribution(results: AuditResult[]): {
  good: number;
  average: number;
  poor: number;
} {
  let good = 0,
    average = 0,
    poor = 0;
  for (const r of results) {
    const score = r.seo_score ?? 0;
    if (score >= 90) good++;
    else if (score >= 50) average++;
    else poor++;
  }
  return { good, average, poor };
}

export function avgScore(results: AuditResult[]): number {
  if (!results.length) return 0;
  return results.reduce((sum, r) => sum + (r.seo_score ?? 0), 0) / results.length;
}

export function allIssuesOf(results: AuditResult[]): Issue[] {
  return results.flatMap((r) => r.all_issues || []);
}

// Severity ordering, most severe first. Mirrors modules/ai_assist.py _SEVERITY_RANK.
const SEVERITY_RANK: Record<string, number> = {
  Critical: 0,
  High: 1,
  Medium: 2,
  Warning: 3,
  Low: 4,
};

export interface AggregatedIssue {
  issue: string;
  severity: string;
  category: string;
  impact_score: number;
  /** number of DISTINCT pages the issue was found on */
  count: number;
  /** the actual affected page URLs, so the UI can list them (not just "N pages") */
  urls: string[];
}

/**
 * Group issues across a multi-URL audit by issue title, recording the exact
 * affected-page URLs. Previously the sitewide rollup only counted how many
 * pages tripped an issue ("N pages") but threw the page attribution away, so a
 * user could see "180 pages: Missing meta description" with no way to find out
 * WHICH pages. This keeps the URL list so the Results view can list them.
 */
export function issuesByTitle(results: AuditResult[]): AggregatedIssue[] {
  const map = new Map<string, AggregatedIssue>();
  for (const r of results) {
    const seenOnPage = new Set<string>();
    for (const i of r.all_issues || []) {
      const title = i.issue;
      let e = map.get(title);
      if (!e) {
        e = {
          issue: title,
          severity: i.severity,
          category: i.category,
          impact_score: i.impact_score || 0,
          count: 0,
          urls: [],
        };
        map.set(title, e);
      }
      // Count each page once per issue title, even if the page emits it twice.
      if (!seenOnPage.has(title)) {
        seenOnPage.add(title);
        e.count++;
        e.urls.push(r.url);
      }
      if ((SEVERITY_RANK[i.severity] ?? 4) < (SEVERITY_RANK[e.severity] ?? 4)) {
        e.severity = i.severity;
      }
    }
  }
  return [...map.values()].sort(
    (a, b) =>
      (SEVERITY_RANK[a.severity] ?? 4) - (SEVERITY_RANK[b.severity] ?? 4) ||
      b.count - a.count ||
      b.impact_score - a.impact_score,
  );
}
