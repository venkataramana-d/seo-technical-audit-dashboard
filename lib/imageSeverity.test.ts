import { describe, it, expect } from "vitest";
import { explainImageIssue, type ImageEntry } from "./imageAnalysis";

function img(overrides: Partial<ImageEntry> = {}): ImageEntry {
  return {
    url: "https://cdn.example.com/pic.jpg",
    name: "pic.jpg",
    extension: "jpg",
    format_label: "JPEG",
    alt_text: null,
    alt_status: "ok",
    has_lazy: true,
    width: null,
    height: null,
    has_dimensions: true,
    has_srcset: false,
    is_in_picture: false,
    naming_quality: "good",
    file_size_bytes: 10000,
    file_size_label: "10 KB",
    status_code: 200,
    is_broken: false,
    fetch_error: null,
    is_lcp_candidate: false,
    issues: [],
    sourceUrl: "https://example.com/page",
    ...overrides,
  };
}

// Per-image explanation severities must match the bands emitted by
// modules/image_auditor.py after the Phase-1 recalibration. If the backend
// changes a band, this test should be updated in lockstep so the detail panel
// never silently drifts from what the audit actually reports.
describe("explainImageIssue severities are synced with the backend", () => {
  const cases: [string, string][] = [
    ["Missing alt text", "High"],
    ["Empty alt text", "Low"],
    ["Keyword-stuffed alt text", "Warning"],
    ["Missing lazy loading", "Notice"],
    ["Missing width/height dimensions", "Medium"],
    ["Broken image (does not load)", "Critical"],
    ["Large file size (> 300KB)", "Warning"],
  ];

  it.each(cases)("%s → %s", (issue, expected) => {
    const exp = explainImageIssue(issue, img());
    expect(exp).not.toBeNull();
    expect(exp!.severity).toBe(expected);
  });
});
