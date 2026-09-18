"use client";

import { useState } from "react";
import { Card } from "@/components/ui";
import { CHECK_DEFS } from "@/lib/checklistDefs";
import { ListChecksIcon } from "@/components/icons";

/**
 * "What Technical SEO checks" explainer, mirroring the reference tool's
 * plain-English use-case card: description, every check as a pill, and a
 * "when to use" callout. Collapsed by default so it doesn't push the actual
 * audit form below the fold.
 */
export function ChecklistExplainer() {
  const [open, setOpen] = useState(false);

  return (
    <Card className="mb-4 border-l-4 border-l-[var(--seo-accent)]">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full items-center justify-between gap-2 text-left"
      >
        <span className="flex items-center gap-2">
          <ListChecksIcon size={16} className="text-[var(--seo-accent)]" />
          <h3 className="text-sm font-semibold text-[var(--seo-accent)]">What Technical SEO checks</h3>
        </span>
        <span className={`text-[var(--seo-muted)] transition-transform ${open ? "rotate-90" : ""}`}>▸</span>
      </button>

      {open ? (
        <div className="mt-3">
          <p className="mb-3 text-sm text-[var(--seo-text-light)]">
            A comprehensive technical audit combining crawlability (12 checks), on-page (11 checks), and
            site health (12 checks) into a single 35-check run: the fastest way to get a complete
            technical picture of any URL, with no API key required.
          </p>
          <div className="flex flex-wrap gap-1.5">
            {CHECK_DEFS.map((c) => (
              <span
                key={c.id}
                title={c.description}
                className="pill cursor-default"
                style={{ color: "var(--seo-accent)", backgroundColor: "var(--seo-accent-light)" }}
              >
                {c.label}
              </span>
            ))}
          </div>
          <div className="mt-3 rounded-lg border-l-2 border-l-[var(--seo-accent)] bg-[var(--seo-card-bg-alt)] px-3 py-2 text-xs text-[var(--seo-text-light)]">
            <strong className="text-[var(--seo-subheading)]">When to use:</strong> run this as your
            default first audit on any new URL or client site. It covers everything you need before
            publishing, after a site migration, or for a technical SEO proposal.
          </div>
        </div>
      ) : (
        <p className="mt-1 text-xs text-[var(--seo-muted)]">
          35 checks across crawlability, on-page, and site health. No API key required.
        </p>
      )}
    </Card>
  );
}
