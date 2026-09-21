import { describe, expect, it } from "vitest";
import { AFFECTED_CAP, getThematicIssues, issuesByTitle } from "./aggregate";
import type { AuditResult, Issue } from "./types";

const mkIssue = (over: Partial<Issue>): Issue => ({
  issue: "",
  category: "",
  severity: "Medium",
  recommendation: "",
  impact_score: 5,
  effort: "Low",
  ...over,
});

const mkResult = (url: string, issues: Issue[]): AuditResult =>
  ({ url, all_issues: issues } as unknown as AuditResult);

describe("issuesByTitle", () => {
  it("groups by title and records the exact affected page URLs", () => {
    const results = [
      mkResult("https://x.com/a", [mkIssue({ issue: "Missing meta description" })]),
      mkResult("https://x.com/b", [mkIssue({ issue: "Missing meta description" })]),
      mkResult("https://x.com/c", [mkIssue({ issue: "H1 too long", severity: "Warning" })]),
    ];
    const agg = issuesByTitle(results);
    const md = agg.find((a) => a.issue === "Missing meta description")!;
    expect(md.count).toBe(2);
    expect(md.urls).toEqual(["https://x.com/a", "https://x.com/b"]);
  });

  it("counts a page once per title even if it emits the issue twice", () => {
    const results = [
      mkResult("https://x.com/a", [
        mkIssue({ issue: "Broken link" }),
        mkIssue({ issue: "Broken link" }),
      ]),
    ];
    const agg = issuesByTitle(results);
    expect(agg[0].count).toBe(1);
    expect(agg[0].urls).toEqual(["https://x.com/a"]);
  });

  it("merges instance-count variants of the same issue into one row", () => {
    const results = [
      mkResult("https://x.com/a", [
        mkIssue({ issue: "Missing alt text on 1 image(s)", category: "Images" }),
      ]),
      mkResult("https://x.com/b", [
        mkIssue({ issue: "Missing alt text on 2 image(s)", category: "Images" }),
      ]),
      mkResult("https://x.com/c", [
        mkIssue({ issue: "Missing alt text on 5 image(s)", category: "Images" }),
      ]),
    ];
    const agg = issuesByTitle(results);
    const rows = agg.filter((a) => a.issue === "Missing alt text");
    expect(rows).toHaveLength(1);
    expect(rows[0].count).toBe(3);
    expect(rows[0].urls).toEqual(["https://x.com/a", "https://x.com/b", "https://x.com/c"]);
    // The clean display title should drop the count fragment.
    expect(rows[0].issue).toBe("Missing alt text");
  });

  it("also strips trailing count parentheticals like '(123 chars)'", () => {
    const results = [
      mkResult("https://x.com/a", [
        mkIssue({ issue: "Meta Title Too Long (123 chars)", category: "Metadata" }),
      ]),
      mkResult("https://x.com/b", [
        mkIssue({ issue: "Meta Title Too Long (140 chars)", category: "Metadata" }),
      ]),
    ];
    const agg = issuesByTitle(results);
    expect(agg).toHaveLength(1);
    expect(agg[0].issue).toBe("Meta Title Too Long");
    expect(agg[0].count).toBe(2);
  });

  it("does NOT over-merge issues with different categories or stems", () => {
    const results = [
      mkResult("https://x.com/a", [
        mkIssue({ issue: "Missing alt text on 1 image(s)", category: "Images" }),
        // Same stem text but a different category must stay a separate row.
        mkIssue({ issue: "Missing alt text on 1 image(s)", category: "Accessibility" }),
        // Different stem must stay separate.
        mkIssue({ issue: "Broken link (1)", category: "Images" }),
      ]),
    ];
    const agg = issuesByTitle(results);
    expect(agg).toHaveLength(3);
    expect(agg.map((a) => `${a.category}:${a.issue}`).sort()).toEqual([
      "Accessibility:Missing alt text",
      "Images:Broken link",
      "Images:Missing alt text",
    ]);
  });

  it("carries the affected elements up, tagged with their source page URL", () => {
    const results = [
      mkResult("https://x.com/a", [
        mkIssue({
          issue: "Missing alt text on 2 image(s)",
          category: "Images",
          affected: [
            { value: "/logo.png" },
            { value: "/hero.jpg", detail: "above the fold" },
          ],
        }),
      ]),
      mkResult("https://x.com/b", [
        mkIssue({
          issue: "Missing alt text on 1 image(s)",
          category: "Images",
          affected: [{ value: "/banner.gif" }],
        }),
      ]),
    ];
    const agg = issuesByTitle(results);
    expect(agg).toHaveLength(1);
    expect(agg[0].affected).toEqual([
      { url: "https://x.com/a", value: "/logo.png", detail: undefined },
      { url: "https://x.com/a", value: "/hero.jpg", detail: "above the fold" },
      { url: "https://x.com/b", value: "/banner.gif", detail: undefined },
    ]);
  });

  it("caps the merged affected list at AFFECTED_CAP", () => {
    const many = Array.from({ length: 500 }, (_, n) => ({ value: `/img-${n}.png` }));
    const results = [
      mkResult("https://x.com/a", [
        mkIssue({ issue: "Missing alt text on 500 image(s)", category: "Images", affected: many }),
      ]),
    ];
    const agg = issuesByTitle(results);
    expect(agg[0].affected).toHaveLength(AFFECTED_CAP);
  });

  it("sorts the most severe issue first even when it affects fewer pages", () => {
    const results = [
      mkResult("https://x.com/a", [mkIssue({ issue: "Minor", severity: "Low" })]),
      mkResult("https://x.com/b", [mkIssue({ issue: "Minor", severity: "Low" })]),
      mkResult("https://x.com/c", [mkIssue({ issue: "Critical thing", severity: "Critical" })]),
    ];
    const agg = issuesByTitle(results);
    expect(agg[0].issue).toBe("Critical thing");
  });
});

describe("getThematicIssues category mapping", () => {
  it("does not drop heading/security/mobile-UX categories into Other", () => {
    const issues = [
      "Heading Structure",
      "Security",
      "Responsiveness",
      "Usability",
      "Navigation",
      "User Experience",
      "Layout",
    ].map((category) => mkIssue({ issue: `${category} issue`, category }));
    const grouped = getThematicIssues(issues);
    expect(grouped.Other).toBeUndefined();
  });
});
