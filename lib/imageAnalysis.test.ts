import { describe, it, expect } from "vitest";
import { deriveImageAffected, explainImageIssue, imagePriorityScore, type ImageEntry } from "./imageAnalysis";

function img(overrides: Partial<ImageEntry> = {}): ImageEntry {
  return {
    url: "https://cdn.example.com/pic.webp",
    name: "pic.webp",
    extension: "webp",
    format_label: "WebP",
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

// The backend per-image tag and issue title for oversized images use "300KB"
// (Phase 1 raised the threshold from 200KB). These tests lock the frontend to
// the SAME string so the explanation / priority / affected paths can't silently
// drift back to 200KB and break large-image handling.
describe("large-image label is synced with the backend (300KB)", () => {
  const big = img({ file_size_bytes: 400 * 1024, issues: ["Large file size (> 300KB)"] });

  it("explainImageIssue resolves the 300KB tag", () => {
    const exp = explainImageIssue("Large file size (> 300KB)", big);
    expect(exp).not.toBeNull();
    expect(exp!.issueName).toBe("Large File Size");
  });

  it("the old 200KB tag no longer resolves", () => {
    expect(explainImageIssue("Large file size (> 200KB)", big)).toBeNull();
  });

  it("imagePriorityScore counts the 300KB tag", () => {
    expect(imagePriorityScore(big)).toBeGreaterThanOrEqual(40);
  });

  it("deriveImageAffected finds large images from the '...larger than 300KB' title", () => {
    const affected = deriveImageAffected("2 image(s) are larger than 300KB", [big, img()]);
    expect(affected.map((a) => a.value)).toEqual([big.url]);
  });
});

describe("deriveImageAffected maps issue titles to the exact offending images", () => {
  const missingAlt = img({ url: "https://cdn/x1.webp", alt_status: "missing", issues: ["Missing alt text"] });
  const dupA = img({ url: "https://cdn/a.webp", alt_text: "Shared", issues: [] });
  const dupB = img({ url: "https://cdn/b.webp", alt_text: "Shared", issues: [] });
  const unique = img({ url: "https://cdn/c.webp", alt_text: "Unique", issues: [] });
  const all = [missingAlt, dupA, dupB, unique];

  it("missing alt", () => {
    expect(deriveImageAffected("Missing alt text on 1 image(s)", all).map((a) => a.value)).toEqual([missingAlt.url]);
  });

  it("duplicate alt groups only the shared-alt images", () => {
    const vals = deriveImageAffected("Duplicate alt text on 2 image(s)", all).map((a) => a.value);
    expect(vals.sort()).toEqual([dupA.url, dupB.url].sort());
  });

  it("returns [] for a non-image issue", () => {
    expect(deriveImageAffected("Title tag missing", all)).toEqual([]);
  });
});
