// Classify an audited URL into a human page-category ("Type" column on the
// Results table). Primary signal is the first URL path segment (reliable - most
// sites organise by directory: /course/…, /blog/…, /topic/…, /tag/…); the
// backend's own `audit_type` (course/blog/general, from content + URL) is a
// secondary fallback. This replaces the old domain/section hierarchy on the
// Results page with a flat, filterable "Type" column.

const SEGMENT_TO_CATEGORY: Record<string, string> = {
  course: "Course",
  courses: "Course",
  training: "Course",
  program: "Course",
  programs: "Course",
  workshop: "Course",
  bootcamp: "Course",
  blog: "Blog",
  blogs: "Blog",
  article: "Blog",
  articles: "Blog",
  post: "Blog",
  posts: "Blog",
  news: "Blog",
  insight: "Blog",
  insights: "Blog",
  topic: "Topic",
  topics: "Topic",
  category: "Category",
  categories: "Category",
  tag: "Tag",
  tags: "Tag",
  type: "Type",
  types: "Type",
  corporate: "Corporate",
};

/** The category label shown in the Results "Type" column. */
export function categorizeUrl(url: string, auditType?: string): string {
  let path = "";
  try {
    path = new URL(url).pathname;
  } catch {
    path = url;
  }
  const segment = path.split("/").filter(Boolean)[0];
  if (!segment) return "Home";

  const mapped = SEGMENT_TO_CATEGORY[segment.toLowerCase()];
  if (mapped) return mapped;

  // Content-derived fallback (backend page-type) when the URL segment isn't a
  // recognised directory - a course/blog page on a non-standard path.
  if (auditType === "course") return "Course";
  if (auditType === "blog") return "Blog";

  // A single top-level segment with no recognised keyword is a static page
  // (about-us, pricing, contact, …).
  return "Static";
}

/** Tailwind-friendly color per category, via the app's CSS tokens. */
export function categoryColor(category: string): { text: string; bg: string } {
  switch (category) {
    case "Course":
      return { text: "var(--seo-accent)", bg: "var(--seo-accent-light)" };
    case "Blog":
      return { text: "var(--seo-success)", bg: "var(--seo-success-bg)" };
    case "Home":
      return { text: "var(--seo-subheading)", bg: "var(--seo-card-hover)" };
    case "Topic":
    case "Category":
    case "Tag":
    case "Type":
      return { text: "var(--seo-warning)", bg: "var(--seo-warning-bg)" };
    default:
      return { text: "var(--seo-text-light)", bg: "var(--seo-card-hover)" };
  }
}
