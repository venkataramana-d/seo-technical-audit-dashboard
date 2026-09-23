import type { AuditResult } from "@/lib/types";
import { STATUS_COLOR_HEX, type StatusColor } from "@/lib/linkAnalysis";

export interface ImageEntry {
  url: string;
  name: string;
  extension: string;
  format_label: string;
  alt_text: string | null;
  alt_status: "missing" | "empty" | "generic" | "keyword_stuffed" | "ok";
  has_lazy: boolean;
  width: number | null;
  height: number | null;
  has_dimensions: boolean;
  has_srcset: boolean;
  is_in_picture: boolean;
  naming_quality: "good" | "bad";
  file_size_bytes: number | null;
  file_size_label: string;
  status_code: number | null;
  is_broken: boolean | null;
  fetch_error: string | null;
  is_lcp_candidate: boolean;
  issues: string[];
  sourceUrl: string;
}

export function flattenImages(results: AuditResult[]): ImageEntry[] {
  return results.flatMap((r) => {
    const images: ImageEntry[] = r.image_detail?.images || [];
    return images.map((img) => ({ ...img, sourceUrl: r.url }));
  });
}

export interface FormatStat {
  format: string;
  count: number;
  pct: number;
}

export function formatBreakdown(images: ImageEntry[]): FormatStat[] {
  const counts = new Map<string, number>();
  for (const img of images) counts.set(img.format_label, (counts.get(img.format_label) || 0) + 1);
  const total = images.length || 1;
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1])
    .map(([format, count]) => ({ format, count, pct: Math.round((count / total) * 1000) / 10 }));
}

export function formatBytes(bytes: number | null): string {
  if (bytes == null) return "N/A";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

export const ALT_STATUS_LABEL: Record<ImageEntry["alt_status"], string> = {
  missing: "Missing",
  empty: "Empty",
  generic: "Generic",
  keyword_stuffed: "Keyword-stuffed",
  ok: "OK",
};

export interface ImageIssueExplanation {
  issueName: string;
  status: StatusColor;
  // Widened to include "Notice"/"Warning" so per-image severities stay in
  // lockstep with modules/image_auditor.py, which emits those two bands too
  // (Notice = missing lazy loading; Warning = keyword-stuffed alt / large file).
  severity: "Critical" | "High" | "Medium" | "Low" | "Notice" | "Warning";
  whatIsIt: string;
  whyImportant: string;
  seoImpact: string;
  userImpact: string;
  recommendedFix: string;
  htmlExample?: string;
}

// Deterministic (rule-based) per-issue explanation, one entry per string in
// image.issues: mirrors the shape of lib/linkAnalysis.ts's explainLink.
export function explainImageIssue(issue: string, img: ImageEntry): ImageIssueExplanation | null {
  const name = img.name || "this image";
  switch (issue) {
    case "Missing alt text":
      return {
        issueName: "Missing Alt Text",
        status: "critical",
        severity: "High",
        whatIsIt: `"${name}" has no alt attribute at all.`,
        whyImportant: "Alt text is how search engines and screen readers understand what an image shows; without it, the image contributes nothing to SEO and is invisible to visually impaired visitors.",
        seoImpact: "The image can't rank in Google Image Search and provides no contextual relevance signal for the page.",
        userImpact: "Screen reader users hear nothing when they reach this image; if it fails to load, sighted users see a blank box instead of a description.",
        recommendedFix: "Add a concise, descriptive alt attribute describing what the image shows and its purpose on the page.",
        htmlExample: `<img src="${img.url}" alt="Descriptive text about the image">`,
      };
    case "Empty alt text":
      return {
        issueName: "Empty Alt Text",
        status: "warning",
        severity: "Low",
        whatIsIt: `"${name}" has alt="" (valid only if the image is purely decorative).`,
        whyImportant: "An empty alt is the correct choice for decorative images, but if this image conveys real content, it's being skipped entirely by screen readers and search engines.",
        seoImpact: "No SEO value from this image; correct if decorative, a missed opportunity if not.",
        userImpact: "Screen readers skip the image silently: fine for decoration, a gap if it's meaningful.",
        recommendedFix: "If decorative, leave as-is. If it conveys information, add descriptive alt text.",
        htmlExample: `<img src="${img.url}" alt="Descriptive text (or leave empty only if purely decorative)">`,
      };
    case "Generic alt text":
      return {
        issueName: "Generic Alt Text",
        status: "info",
        severity: "Low",
        whatIsIt: `"${name}" uses a generic alt value like "image" or "photo" instead of a real description.`,
        whyImportant: "Generic alt text carries no information about the image's actual content or relevance to the page topic.",
        seoImpact: "Missed opportunity for a contextual relevance signal to the surrounding content.",
        userImpact: "Screen reader users hear a meaningless word instead of knowing what's actually shown.",
        recommendedFix: "Replace with a specific description of what the image depicts.",
        htmlExample: `<img src="${img.url}" alt="${img.alt_text || "photo"} of [specific subject]">`,
      };
    case "Keyword-stuffed alt text":
      return {
        issueName: "Keyword-Stuffed Alt Text",
        status: "warning",
        severity: "Warning",
        whatIsIt: `"${name}"'s alt text is unnaturally long or repeats the same word many times.`,
        whyImportant: "Search engines treat over-optimized alt text as a manipulation signal, which can hurt rather than help rankings.",
        seoImpact: "Risk of being flagged as a spam/manipulation signal by search engines.",
        userImpact: "Screen reader users hear an unnaturally long or repetitive description.",
        recommendedFix: "Shorten to a natural, accurate description: a sentence, not a keyword list.",
      };
    case "Missing lazy loading":
      return {
        issueName: "Missing Lazy Loading",
        status: "info",
        severity: "Notice",
        whatIsIt: `"${name}" doesn't use loading="lazy".`,
        whyImportant: "Below-the-fold images without lazy loading are downloaded immediately, competing for bandwidth with content the visitor actually sees first.",
        seoImpact: "Slower page load can affect Core Web Vitals (particularly on image-heavy pages), which is a ranking factor.",
        userImpact: "Slightly slower initial page load, especially on mobile connections.",
        recommendedFix: "Add loading=\"lazy\" to images below the fold. Do not lazy-load the LCP (largest visible) image; it should load immediately.",
        htmlExample: `<img src="${img.url}" loading="lazy" alt="...">`,
      };
    case "Missing width/height dimensions":
      return {
        issueName: "Missing Width/Height Attributes",
        status: "warning",
        severity: "Medium",
        whatIsIt: `"${name}" has no explicit width/height attributes.`,
        whyImportant: "Without dimensions, the browser doesn't know how much space to reserve before the image loads, causing the page layout to jump (Cumulative Layout Shift).",
        seoImpact: "CLS is a Core Web Vital and a direct ranking factor: missing dimensions are one of the most common causes of poor CLS scores.",
        userImpact: "Content visibly jumps around as images pop in, which is disorienting and can cause mis-clicks.",
        recommendedFix: "Add explicit width and height attributes matching the image's intrinsic aspect ratio.",
        htmlExample: `<img src="${img.url}" width="800" height="600" alt="...">`,
      };
    case "Poor filename convention":
      return {
        issueName: "Poor Filename Convention",
        status: "info",
        severity: "Low",
        whatIsIt: `"${name}" uses a generic/auto-generated name (e.g. IMG_1234.jpg) instead of a descriptive one.`,
        whyImportant: "Descriptive filenames are a minor, low-cost image SEO signal Google has confirmed it uses.",
        seoImpact: "Small missed opportunity for an additional relevance signal in Google Image Search.",
        userImpact: "None directly; this only affects search engine understanding.",
        recommendedFix: "Rename to a descriptive, hyphen-separated filename (e.g. red-leather-office-chair.jpg) before uploading.",
      };
    case "Could be converted to WebP/AVIF":
      return {
        issueName: "Legacy Image Format",
        status: "info",
        severity: "Low",
        whatIsIt: `"${name}" is a ${img.format_label}. WebP or AVIF would produce a smaller file at the same visual quality.`,
        whyImportant: "Modern formats typically cut file size 25–50% versus JPEG/PNG with no visible quality loss.",
        seoImpact: "Smaller images load faster, improving LCP (a Core Web Vital and ranking factor).",
        userImpact: "Faster page loads, less mobile data used.",
        recommendedFix: "Re-export as WebP or AVIF, or serve via a <picture> element with a WebP/AVIF source and a JPEG/PNG fallback.",
        htmlExample: `<picture>\n  <source srcset="${img.name.replace(/\.[^.]+$/, ".webp")}" type="image/webp">\n  <img src="${img.url}" alt="...">\n</picture>`,
      };
    case "Broken image (does not load)":
      return {
        issueName: "Broken Image",
        status: "critical",
        severity: "Critical",
        whatIsIt: `"${name}" doesn't load${img.fetch_error ? `: ${img.fetch_error}` : img.status_code ? `: server responded ${img.status_code}` : ""}.`,
        whyImportant: "A broken image shows as a blank box or broken-icon placeholder to every visitor, and search engine crawlers waste crawl budget requesting a resource that fails.",
        seoImpact: "Broken images can't appear in Google Image Search, hurt perceived page quality, and repeated 4xx/5xx image requests waste crawl budget on larger sites.",
        userImpact: "Visitors see a blank/broken placeholder instead of the intended image, which looks unpolished and can undermine trust.",
        recommendedFix: "Check the URL is correct and the file still exists on the server. Fix the path/hosting, or remove the <img> reference if the asset is gone for good.",
      };
    case "Large file size (> 300KB)":
      return {
        issueName: "Large File Size",
        status: "warning",
        severity: "Warning",
        whatIsIt: `"${name}" is ${formatBytes(img.file_size_bytes)}, above the 300KB guideline for web images.`,
        whyImportant: "Large images are consistently the biggest cause of slow page loads on content-heavy pages.",
        seoImpact: "Directly slows LCP and overall page weight, both of which affect Core Web Vitals and rankings.",
        userImpact: "Slower load, especially painful on mobile/slow connections; visitors may leave before the page finishes loading.",
        recommendedFix: "Compress the image, resize to the actual display dimensions, and use a modern format (WebP/AVIF).",
      };
    default:
      return null;
  }
}

// Maps an aggregated image-issue title (e.g. "Missing alt text on 3 image(s)")
// to the per-image `issues` label that identifies the offending images.
const IMAGE_ISSUE_LABELS: { re: RegExp; label: string; detail: string }[] = [
  { re: /^missing alt text/i, label: "Missing alt text", detail: "no alt attribute" },
  { re: /^empty alt text/i, label: "Empty alt text", detail: 'alt=""' },
  { re: /^generic alt text/i, label: "Generic alt text", detail: "generic alt text" },
  { re: /^keyword-stuffed alt/i, label: "Keyword-stuffed alt text", detail: "keyword-stuffed alt" },
  { re: /missing lazy loading/i, label: "Missing lazy loading", detail: "no loading=\"lazy\"" },
  { re: /missing width\/height/i, label: "Missing width/height dimensions", detail: "no width/height" },
  { re: /converted to webp/i, label: "Could be converted to WebP/AVIF", detail: "legacy format" },
  { re: /poor filename/i, label: "Poor filename convention", detail: "poor filename" },
  { re: /fail to load/i, label: "Broken image (does not load)", detail: "does not load" },
  { re: /larger than \d+kb|large file size/i, label: "Large file size (> 300KB)", detail: "over 300KB" },
];

/**
 * Derive the exact offending images for an aggregated image-issue title, read
 * straight from the per-image `image_detail.images` list. This lets the Issues
 * tab point at the specific image src(s) behind a "... on N image(s)" issue even
 * for audits crawled before the backend started attaching `affected`. Returns []
 * for non-image issues, so callers can safely try it on any issue.
 */
export function deriveImageAffected(
  issueTitle: string,
  images: ImageEntry[],
  cap = 200,
): { value: string; detail: string }[] {
  if (!issueTitle || images.length === 0) return [];

  // "Duplicate alt text" has no single per-image label; find images that share
  // a non-empty alt value.
  if (/^duplicate alt text/i.test(issueTitle)) {
    const counts = new Map<string, number>();
    for (const img of images) {
      const alt = (img.alt_text || "").trim();
      if (alt) counts.set(alt, (counts.get(alt) || 0) + 1);
    }
    return images
      .filter((img) => {
        const alt = (img.alt_text || "").trim();
        return alt.length > 0 && (counts.get(alt) || 0) > 1;
      })
      .slice(0, cap)
      .map((img) => ({ value: img.url, detail: `alt="${(img.alt_text || "").trim()}"` }));
  }

  const match = IMAGE_ISSUE_LABELS.find((m) => m.re.test(issueTitle));
  if (!match) return [];
  return images
    .filter(
      (img) =>
        img.issues.includes(match.label) ||
        // Broken images are flagged via is_broken even when the label is absent.
        (match.label === "Broken image (does not load)" && img.is_broken === true),
    )
    .slice(0, cap)
    .map((img) => ({ value: img.url, detail: match.detail }));
}

export function imagePriorityScore(img: ImageEntry): number {
  let score = 0;
  if (img.is_broken) score += 50;
  if (img.issues.includes("Large file size (> 300KB)")) score += 40;
  if (img.issues.includes("Missing alt text")) score += 30;
  if (img.issues.includes("Missing width/height dimensions")) score += 20;
  if (img.is_lcp_candidate) score += 15;
  if (img.issues.includes("Missing lazy loading") && !img.is_lcp_candidate) score += 5;
  return Math.min(100, score);
}

export { STATUS_COLOR_HEX };
