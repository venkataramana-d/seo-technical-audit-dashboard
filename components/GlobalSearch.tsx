"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useAudit } from "@/lib/state/AuditContext";
import { SearchIcon } from "@/components/icons";

// Jumps straight to a previously-audited URL's Detail page from anywhere in
// the app. Filters the in-memory results already held by AuditContext (no
// network call) - same data the Results page's own search box filters.
export function GlobalSearch({ onNavigate }: { onNavigate?: () => void }) {
  const router = useRouter();
  const { results, setSelectedUrlIndex } = useAudit();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    function onClickOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, []);

  const q = query.trim().toLowerCase();
  const matches = q
    ? results
        .map((r, index) => ({ r, index }))
        .filter(({ r }) => r.url.toLowerCase().includes(q))
        .slice(0, 8)
    : [];

  function goTo(index: number) {
    setSelectedUrlIndex(index);
    router.push("/detail");
    setOpen(false);
    setQuery("");
    onNavigate?.();
  }

  return (
    <div ref={containerRef} className="relative w-full max-w-xs">
      <div className="flex items-center gap-2 rounded-lg border border-[var(--seo-border)] bg-[var(--seo-card-bg-alt)] px-3 py-1.5 focus-within:border-[var(--seo-accent-border)]">
        <SearchIcon size={15} className="shrink-0 text-[var(--seo-muted)]" />
        <input
          ref={inputRef}
          type="search"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && matches.length > 0) goTo(matches[0].index);
            if (e.key === "Escape") setOpen(false);
          }}
          placeholder={results.length ? "Search audited URLs…" : "No audited URLs yet"}
          disabled={results.length === 0}
          className="w-full bg-transparent text-sm text-[var(--seo-text)] placeholder:text-[var(--seo-placeholder)] focus:outline-none disabled:cursor-not-allowed"
        />
      </div>

      {open && q ? (
        <div className="absolute left-0 right-0 z-30 mt-1 max-h-72 overflow-y-auto rounded-lg border border-[var(--seo-border)] bg-[var(--seo-card-bg)] py-1 shadow-[var(--seo-shadow-md)]">
          {matches.length === 0 ? (
            <p className="px-3 py-2 text-sm text-[var(--seo-muted)]">No matching audited URLs.</p>
          ) : (
            matches.map(({ r, index }) => (
              <button
                key={r.url + index}
                type="button"
                onClick={() => goTo(index)}
                className="flex w-full items-center justify-between gap-3 px-3 py-2 text-left text-sm text-[var(--seo-text)] hover:bg-[var(--seo-card-hover)]"
              >
                <span className="truncate">{r.url}</span>
                <span className="shrink-0 text-xs font-semibold text-[var(--seo-muted)]">{r.seo_score}</span>
              </button>
            ))
          )}
        </div>
      ) : null}
    </div>
  );
}
