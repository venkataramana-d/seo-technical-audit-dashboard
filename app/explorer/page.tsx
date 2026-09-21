"use client";

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { useAudit } from "@/lib/state/AuditContext";
import { AffectedList, Card, EmptyState, PageHeader, ScoreBadge, SeverityBadge } from "@/components/ui";
import { ExplorerExportBar } from "@/components/ExplorerExportBar";
import { ListChecksIcon, XIcon } from "@/components/icons";
import type { Issue } from "@/lib/types";
import { severityColor } from "@/lib/format";
import type { AuditResult } from "@/lib/types";

// Screaming-Frog-style "Internal" tab: one dense, sortable, filterable row per
// crawled/audited URL. All derivation is done up front in useMemo so the render
// path stays cheap even with 2000+ rows (which are paginated, never all mounted).

const PAGE_SIZE = 50;

// Worst-severity ranking for the per-row issue dot. Higher = more severe.
const SEV_RANK: Record<string, number> = {
  critical: 5,
  high: 4,
  warning: 3,
  medium: 2,
  low: 1,
};

// Severity bucket mapping. The backend emits severity strings
// Critical / High / Warning / Medium / Low (see modules/*.py). We fold them
// into three Screaming-Frog-style buckets for the filter chips:
//   error   <- Critical, High
//   warning <- Warning, Medium
//   notice  <- Low (and any explicit "Notice")
type SevBucket = "error" | "warning" | "notice" | "";

function sevBucket(severity: string): SevBucket {
  switch ((severity ?? "").toLowerCase()) {
    case "critical":
    case "high":
      return "error";
    case "warning":
    case "medium":
      return "warning";
    case "low":
    case "notice":
      return "notice";
    default:
      return "";
  }
}

// --- Filter state shapes -----------------------------------------------------
type SeverityFilter = "all" | "errors" | "warnings" | "notices";
type StatusBand = "all" | "2xx" | "3xx" | "4xx" | "5xx";
type IndexableFilter = "all" | "yes" | "no";

type SortKey =
  | "url"
  | "type"
  | "status"
  | "indexable"
  | "score"
  | "title"
  | "titleLen"
  | "descLen"
  | "h1"
  | "words"
  | "images"
  | "missingAlt"
  | "internal"
  | "brokenInternal"
  | "issues";

type SortDir = "asc" | "desc";

/** Derived, flattened view of one AuditResult - every nested access guarded. */
interface Row {
  r: AuditResult;
  idx: number; // original index in the unfiltered results array
  url: string;
  type: string;
  status: number | null;
  indexable: boolean | null;
  score: number;
  title: string;
  titleLen: number;
  descLen: number;
  h1: number;
  words: number;
  images: number;
  missingAlt: number;
  internal: number;
  brokenInternal: number;
  brokenExternal: number;
  issues: number;
  worstSev: number; // 0 when no issues
  worstSevName: string;
  // Derived filter helpers (computed once in toRow).
  hasError: boolean; // any Critical/High issue
  hasWarning: boolean; // any Warning/Medium issue
  hasNotice: boolean; // any Low/Notice issue
  hasBroken: boolean; // internal or external broken link
  badStatus: boolean; // status_code >= 400
  categories: string[]; // distinct issue categories present on this URL
}

function toRow(r: AuditResult, idx: number): Row {
  const meta = r.metadata ?? {};
  const img = r.image_detail ?? {};
  const images = r.images ?? {};
  const il = r.internal_links ?? {};
  const el = r.external_links ?? {};
  const allIssues = Array.isArray(r.all_issues) ? r.all_issues : [];

  let worstSev = 0;
  let worstSevName = "";
  let hasError = false;
  let hasWarning = false;
  let hasNotice = false;
  const categorySet = new Set<string>();
  for (const iss of allIssues) {
    const rank = SEV_RANK[(iss?.severity ?? "").toLowerCase()] ?? 0;
    if (rank > worstSev) {
      worstSev = rank;
      worstSevName = iss?.severity ?? "";
    }
    switch (sevBucket(iss?.severity ?? "")) {
      case "error":
        hasError = true;
        break;
      case "warning":
        hasWarning = true;
        break;
      case "notice":
        hasNotice = true;
        break;
    }
    const cat = (iss?.category ?? "").trim();
    if (cat) categorySet.add(cat);
  }

  const status = r.status_code ?? null;
  const brokenInternal = il.broken_count ?? 0;
  const brokenExternal = el.broken_count ?? 0;

  return {
    r,
    idx,
    url: r.url ?? "",
    type: r.audit_type ?? "-",
    status,
    indexable:
      typeof r.indexability?.is_indexable === "boolean" ? r.indexability.is_indexable : null,
    score: r.seo_score ?? 0,
    title: meta.title ?? "",
    titleLen: meta.title_length ?? (typeof meta.title === "string" ? meta.title.length : 0),
    descLen:
      meta.description_length ??
      (typeof meta.description === "string" ? meta.description.length : 0),
    h1: meta.h1_count ?? 0,
    words: r.content?.word_count ?? 0,
    // image_detail exposes `total`/`missing_alt`; the legacy `images` section
    // uses `total_images`/`missing_alt_count`. Guard for either shape.
    images: img.total ?? img.total_images ?? images.total_images ?? 0,
    missingAlt: img.missing_alt ?? img.missing_alt_count ?? images.missing_alt_count ?? 0,
    internal: il.total_links ?? 0,
    brokenInternal,
    brokenExternal,
    issues: allIssues.length,
    worstSev,
    worstSevName,
    hasError,
    hasWarning,
    hasNotice,
    hasBroken: brokenInternal + brokenExternal > 0,
    badStatus: status != null && status >= 400,
    categories: [...categorySet],
  };
}

function statusClass(status: number | null): string {
  if (status == null) return "text-[var(--seo-muted)]";
  if (status >= 200 && status < 300) return "text-[var(--seo-success)]";
  if (status >= 300 && status < 400) return "text-[var(--seo-warning)]";
  return "text-[var(--seo-error)]";
}

const SORT_VALUE: Record<SortKey, (row: Row) => number | string> = {
  url: (row) => row.url.toLowerCase(),
  type: (row) => row.type.toLowerCase(),
  status: (row) => row.status ?? -1,
  indexable: (row) => (row.indexable === true ? 1 : row.indexable === false ? 0 : -1),
  score: (row) => row.score,
  title: (row) => row.title.toLowerCase(),
  titleLen: (row) => row.titleLen,
  descLen: (row) => row.descLen,
  h1: (row) => row.h1,
  words: (row) => row.words,
  images: (row) => row.images,
  missingAlt: (row) => row.missingAlt,
  internal: (row) => row.internal,
  brokenInternal: (row) => row.brokenInternal,
  issues: (row) => row.issues,
};

export default function ExplorerPage() {
  const { results, setSelectedUrlIndex } = useAudit();
  const router = useRouter();

  const [search, setSearch] = useState("");
  const [severity, setSeverity] = useState<SeverityFilter>("all");
  // Quick-flag toggles (independent booleans, each ANDs into the result set).
  const [brokenOnly, setBrokenOnly] = useState(false);
  const [noindexOnly, setNoindexOnly] = useState(false);
  const [badStatusOnly, setBadStatusOnly] = useState(false);
  // Dropdown selects.
  const [statusBand, setStatusBand] = useState<StatusBand>("all");
  const [typeFilter, setTypeFilter] = useState("all");
  const [indexableFilter, setIndexableFilter] = useState<IndexableFilter>("all");
  const [categoryFilter, setCategoryFilter] = useState("all");
  const [sortKey, setSortKey] = useState<SortKey>("score");
  // Default sort: worst score first (ascending score).
  const [sortDir, setSortDir] = useState<SortDir>("asc");
  const [page, setPage] = useState(0);
  // Screaming-Frog-style lower pane: the row the user clicked (or null when the
  // pane is dismissed). Holds the derived Row so its stats/issues render without
  // re-deriving; row.r is the underlying AuditResult for "Open full detail".
  const [selectedRow, setSelectedRow] = useState<Row | null>(null);
  // Scroll the detail pane into view when a row is selected - the pane renders
  // below the (paginated) table, so on a tall list it would otherwise open far
  // below the fold and look like the click did nothing. (scrollIntoView is not
  // setState, so this effect is fine under react-hooks/set-state-in-effect.)
  const paneRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (selectedRow) {
      // Instant (not smooth): smooth scroll is a no-op in some rendering
      // contexts, which left the pane below the fold and made a row click look
      // like nothing happened.
      paneRef.current?.scrollIntoView({ block: "start" });
    }
  }, [selectedRow]);

  // Enrich every result once. Original index is captured so row-click can call
  // setSelectedUrlIndex against the unfiltered results array.
  const rows = useMemo(() => results.map((r, i) => toRow(r, i)), [results]);

  const overview = useMemo(() => {
    const total = rows.length;
    const totalIssues = rows.reduce((sum, row) => sum + row.issues, 0);
    const withIssues = rows.filter((row) => row.issues > 0).length;
    const avg = total > 0 ? Math.round(rows.reduce((s, row) => s + row.score, 0) / total) : 0;
    return { total, totalIssues, withIssues, avg };
  }, [rows]);

  // Live counts for the chips. Computed over the FULL result set (not the
  // current filter) so every chip always shows how many URLs match that single
  // criterion - this is what makes the toolbar read clearly.
  const counts = useMemo(() => {
    let errors = 0;
    let warnings = 0;
    let notices = 0;
    let broken = 0;
    let noindex = 0;
    let badStatus = 0;
    for (const row of rows) {
      if (row.hasError) errors++;
      if (row.hasWarning) warnings++;
      if (row.hasNotice) notices++;
      if (row.hasBroken) broken++;
      if (row.indexable === false) noindex++;
      if (row.badStatus) badStatus++;
    }
    return { all: rows.length, errors, warnings, notices, broken, noindex, badStatus };
  }, [rows]);

  // Distinct audit types present, for the Type dropdown.
  const types = useMemo(
    () => [...new Set(rows.map((row) => row.type).filter((t) => t && t !== "-"))].sort(),
    [rows],
  );

  // Distinct issue categories present across all URLs, for the Category dropdown.
  const categories = useMemo(() => {
    const set = new Set<string>();
    for (const row of rows) for (const c of row.categories) set.add(c);
    return [...set].sort();
  }, [rows]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return rows.filter((row) => {
      // Search (URL or title).
      if (q && !row.url.toLowerCase().includes(q) && !row.title.toLowerCase().includes(q)) {
        return false;
      }
      // Severity chip.
      if (severity === "errors" && !row.hasError) return false;
      if (severity === "warnings" && !row.hasWarning) return false;
      if (severity === "notices" && !row.hasNotice) return false;
      // Quick-flag chips.
      if (brokenOnly && !row.hasBroken) return false;
      if (noindexOnly && row.indexable !== false) return false;
      if (badStatusOnly && !row.badStatus) return false;
      // Status band select.
      if (statusBand !== "all") {
        const s = row.status;
        if (s == null) return false;
        if (statusBand === "2xx" && !(s >= 200 && s < 300)) return false;
        if (statusBand === "3xx" && !(s >= 300 && s < 400)) return false;
        if (statusBand === "4xx" && !(s >= 400 && s < 500)) return false;
        if (statusBand === "5xx" && !(s >= 500 && s < 600)) return false;
      }
      // Type select.
      if (typeFilter !== "all" && row.type !== typeFilter) return false;
      // Indexable select.
      if (indexableFilter === "yes" && row.indexable !== true) return false;
      if (indexableFilter === "no" && row.indexable !== false) return false;
      // Issue-category select.
      if (categoryFilter !== "all" && !row.categories.includes(categoryFilter)) return false;
      return true;
    });
  }, [
    rows,
    search,
    severity,
    brokenOnly,
    noindexOnly,
    badStatusOnly,
    statusBand,
    typeFilter,
    indexableFilter,
    categoryFilter,
  ]);

  const anyFilterActive =
    search.trim() !== "" ||
    severity !== "all" ||
    brokenOnly ||
    noindexOnly ||
    badStatusOnly ||
    statusBand !== "all" ||
    typeFilter !== "all" ||
    indexableFilter !== "all" ||
    categoryFilter !== "all";

  const sorted = useMemo(() => {
    const getVal = SORT_VALUE[sortKey];
    const dir = sortDir === "asc" ? 1 : -1;
    return [...filtered].sort((a, b) => {
      const va = getVal(a);
      const vb = getVal(b);
      if (typeof va === "string" && typeof vb === "string") return va.localeCompare(vb) * dir;
      return ((va as number) - (vb as number)) * dir;
    });
  }, [filtered, sortKey, sortDir]);

  const totalPages = Math.max(1, Math.ceil(sorted.length / PAGE_SIZE));
  // Clamp during render (no effect-based setState) in case a filter shrank the set.
  const safePage = Math.min(page, totalPages - 1);
  const start = safePage * PAGE_SIZE;
  const pageRows = sorted.slice(start, start + PAGE_SIZE);

  function changeSort(key: SortKey) {
    if (key === sortKey) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      // Text columns read best A→Z; numeric columns worst/highest-interest first.
      setSortDir(key === "url" || key === "type" || key === "title" ? "asc" : "desc");
    }
    setPage(0);
  }

  function onSearch(value: string) {
    setSearch(value);
    setPage(0);
  }

  // Severity chips are single-select; clicking the active one (or "All") clears.
  function applySeverity(key: SeverityFilter) {
    setSeverity((prev) => (prev === key ? "all" : key));
    setPage(0);
  }

  function toggleBroken() {
    setBrokenOnly((v) => !v);
    setPage(0);
  }

  function toggleNoindex() {
    setNoindexOnly((v) => !v);
    setPage(0);
  }

  function toggleBadStatus() {
    setBadStatusOnly((v) => !v);
    setPage(0);
  }

  function onStatusBand(value: StatusBand) {
    setStatusBand(value);
    setPage(0);
  }

  function onTypeFilter(value: string) {
    setTypeFilter(value);
    setPage(0);
  }

  function onIndexableFilter(value: IndexableFilter) {
    setIndexableFilter(value);
    setPage(0);
  }

  function onCategoryFilter(value: string) {
    setCategoryFilter(value);
    setPage(0);
  }

  function clearAllFilters() {
    setSearch("");
    setSeverity("all");
    setBrokenOnly(false);
    setNoindexOnly(false);
    setBadStatusOnly(false);
    setStatusBand("all");
    setTypeFilter("all");
    setIndexableFilter("all");
    setCategoryFilter("all");
    setPage(0);
  }

  // Row click: open the inline detail pane (does NOT navigate away). Only rows
  // currently visible in the grid are clickable, so a filtered-out row can never
  // be selected.
  function selectRow(row: Row) {
    setSelectedRow(row);
  }

  // Pane's "Open full detail" button: jump to the standalone Detail page. Uses
  // the row's captured original index into the unfiltered results array.
  function openDetail(row: Row) {
    setSelectedUrlIndex(results.indexOf(row.r));
    router.push("/detail");
  }

  if (results.length === 0) {
    return (
      <div>
        <PageHeader icon={<ListChecksIcon size={18} />} title="Explorer" />
        <EmptyState
          title="No crawled URLs yet"
          hint="Run an audit or site crawl to populate the URL explorer."
        />
        <div className="mt-4 flex justify-center">
          <button
            type="button"
            onClick={() => router.push("/technical-audit")}
            className="rounded-lg btn-gradient px-4 py-2 text-sm font-semibold text-white"
          >
            New Audit
          </button>
        </div>
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        icon={<ListChecksIcon size={18} />}
        title="Explorer"
        subtitle={`Showing ${sorted.length.toLocaleString()} of ${overview.total.toLocaleString()} URLs`}
      />

      {/* Overview strip */}
      <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
        <OverviewStat label="Total URLs" value={overview.total.toLocaleString()} />
        <OverviewStat label="Avg score" value={String(overview.avg)} />
        <OverviewStat label="Total issues" value={overview.totalIssues.toLocaleString()} />
        <OverviewStat label="URLs with issues" value={overview.withIssues.toLocaleString()} />
      </div>

      {/* Export the current (filtered) view */}
      <div className="mb-4">
        <ExplorerExportBar filtered={sorted.map((row) => row.r)} all={results} />
      </div>

      {/* Filter toolbar */}
      <Card className="mb-4">
        <div className="flex flex-col gap-3.5">
          {/* Search */}
          <input
            type="text"
            value={search}
            onChange={(e) => onSearch(e.target.value)}
            placeholder="Search by URL or title…"
            className="w-full rounded-lg border border-[var(--seo-border)] bg-[var(--seo-card-bg)] px-3 py-2 text-sm text-[var(--seo-text)] placeholder:text-[var(--seo-muted)]"
          />

          {/* Row 1: severity + quick-flag chips (with live counts) */}
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="mr-1 text-[11px] font-semibold uppercase tracking-wider text-[var(--seo-muted)]">
              Severity
            </span>
            <FilterChip
              label="All"
              count={counts.all}
              active={severity === "all"}
              onClick={() => applySeverity("all")}
            />
            <FilterChip
              label="Errors"
              count={counts.errors}
              active={severity === "errors"}
              tone="error"
              onClick={() => applySeverity("errors")}
            />
            <FilterChip
              label="Warnings"
              count={counts.warnings}
              active={severity === "warnings"}
              tone="warning"
              onClick={() => applySeverity("warnings")}
            />
            <FilterChip
              label="Notices"
              count={counts.notices}
              active={severity === "notices"}
              tone="notice"
              onClick={() => applySeverity("notices")}
            />

            <span className="mx-1 hidden h-5 w-px bg-[var(--seo-border)] sm:inline-block" />

            <span className="mr-1 text-[11px] font-semibold uppercase tracking-wider text-[var(--seo-muted)]">
              Flags
            </span>
            <FilterChip
              label="Broken links"
              count={counts.broken}
              active={brokenOnly}
              tone="error"
              onClick={toggleBroken}
            />
            <FilterChip
              label="Noindex"
              count={counts.noindex}
              active={noindexOnly}
              tone="warning"
              onClick={toggleNoindex}
            />
            <FilterChip
              label="4xx / 5xx"
              count={counts.badStatus}
              active={badStatusOnly}
              tone="error"
              onClick={toggleBadStatus}
            />
          </div>

          {/* Row 2: dropdown selects */}
          <div className="flex flex-wrap items-end gap-x-4 gap-y-3">
            <SelectFilter
              label="Status"
              value={statusBand}
              onChange={(v) => onStatusBand(v as StatusBand)}
              options={[
                { value: "all", label: "All statuses" },
                { value: "2xx", label: "2xx (OK)" },
                { value: "3xx", label: "3xx (Redirect)" },
                { value: "4xx", label: "4xx (Client error)" },
                { value: "5xx", label: "5xx (Server error)" },
              ]}
            />
            <SelectFilter
              label="Type"
              value={typeFilter}
              onChange={onTypeFilter}
              options={[
                { value: "all", label: "All types" },
                ...types.map((t) => ({ value: t, label: t })),
              ]}
            />
            <SelectFilter
              label="Indexable"
              value={indexableFilter}
              onChange={(v) => onIndexableFilter(v as IndexableFilter)}
              options={[
                { value: "all", label: "All" },
                { value: "yes", label: "Yes" },
                { value: "no", label: "No" },
              ]}
            />
            <SelectFilter
              label="Issue category"
              value={categoryFilter}
              onChange={onCategoryFilter}
              options={[
                { value: "all", label: "All categories" },
                ...categories.map((c) => ({ value: c, label: c })),
              ]}
            />
            {anyFilterActive ? (
              <button
                type="button"
                onClick={clearAllFilters}
                className="ml-auto rounded-lg border border-[var(--seo-border-strong)] px-3 py-1.5 text-xs font-medium text-[var(--seo-text-light)] transition-colors hover:bg-[var(--seo-card-hover)]"
              >
                Clear all filters
              </button>
            ) : null}
          </div>
        </div>
      </Card>

      {/* The grid */}
      <Card className="mb-4 overflow-hidden p-0">
        {sorted.length === 0 ? (
          <div className="px-4 py-10 text-center text-sm text-[var(--seo-muted)]">
            No URLs match the current filters.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-[var(--seo-border)] bg-[var(--table-header-bg)] text-left text-xs uppercase tracking-wide text-[var(--seo-muted)]">
                  <SortHeader label="Address" col="url" sortKey={sortKey} sortDir={sortDir} onSort={changeSort} />
                  <SortHeader label="Type" col="type" sortKey={sortKey} sortDir={sortDir} onSort={changeSort} />
                  <SortHeader label="Status" col="status" sortKey={sortKey} sortDir={sortDir} onSort={changeSort} align="right" />
                  <SortHeader label="Indexable" col="indexable" sortKey={sortKey} sortDir={sortDir} onSort={changeSort} />
                  <SortHeader label="Score" col="score" sortKey={sortKey} sortDir={sortDir} onSort={changeSort} align="right" />
                  <SortHeader label="Title" col="title" sortKey={sortKey} sortDir={sortDir} onSort={changeSort} />
                  <SortHeader label="Title Len" col="titleLen" sortKey={sortKey} sortDir={sortDir} onSort={changeSort} align="right" />
                  <SortHeader label="Desc Len" col="descLen" sortKey={sortKey} sortDir={sortDir} onSort={changeSort} align="right" />
                  <SortHeader label="H1" col="h1" sortKey={sortKey} sortDir={sortDir} onSort={changeSort} align="right" />
                  <SortHeader label="Words" col="words" sortKey={sortKey} sortDir={sortDir} onSort={changeSort} align="right" />
                  <SortHeader label="Img / Missing" col="images" sortKey={sortKey} sortDir={sortDir} onSort={changeSort} align="right" />
                  <SortHeader label="Int / Broken" col="internal" sortKey={sortKey} sortDir={sortDir} onSort={changeSort} align="right" />
                  <SortHeader label="Issues" col="issues" sortKey={sortKey} sortDir={sortDir} onSort={changeSort} align="right" />
                </tr>
              </thead>
              <tbody>
                {pageRows.map((row) => {
                  const isSelected = selectedRow?.idx === row.idx;
                  return (
                  <tr
                    key={`${row.url}-${row.idx}`}
                    onClick={() => selectRow(row)}
                    aria-selected={isSelected}
                    className={`cursor-pointer border-b border-[var(--table-row-border)] last:border-0 ${
                      isSelected
                        ? "bg-[var(--seo-accent-bg,var(--seo-card-hover))] ring-1 ring-inset ring-[var(--seo-accent)]"
                        : "hover:bg-[var(--table-row-hover)]"
                    }`}
                  >
                    <td className="max-w-[22rem] px-3 py-2.5 align-top font-medium text-[var(--seo-subheading)]">
                      <span className="block truncate" title={row.url}>
                        {row.url}
                      </span>
                    </td>
                    <td className="px-3 py-2.5 align-top text-[var(--seo-text-light)]">
                      <span className="pill bg-[var(--seo-card-hover)] text-[var(--seo-text-light)]">
                        {row.type}
                      </span>
                    </td>
                    <td className={`px-3 py-2.5 text-right align-top font-medium tabular-nums ${statusClass(row.status)}`}>
                      {row.status ?? "—"}
                    </td>
                    <td className="px-3 py-2.5 align-top">
                      {row.indexable == null ? (
                        <span className="text-[var(--seo-muted)]">—</span>
                      ) : row.indexable ? (
                        <span className="text-[var(--seo-success)]">Yes</span>
                      ) : (
                        <span className="text-[var(--seo-error)]">No</span>
                      )}
                    </td>
                    <td className="px-3 py-2.5 text-right align-top">
                      <ScoreBadge score={row.score} />
                    </td>
                    <td className="max-w-[18rem] px-3 py-2.5 align-top text-[var(--seo-text-light)]">
                      <span className="block truncate" title={row.title || undefined}>
                        {row.title || <span className="text-[var(--seo-muted)]">—</span>}
                      </span>
                    </td>
                    <td className="px-3 py-2.5 text-right align-top tabular-nums text-[var(--seo-text-light)]">
                      {row.titleLen}
                    </td>
                    <td className="px-3 py-2.5 text-right align-top tabular-nums text-[var(--seo-text-light)]">
                      {row.descLen}
                    </td>
                    <td className="px-3 py-2.5 text-right align-top tabular-nums text-[var(--seo-text-light)]">
                      {row.h1}
                    </td>
                    <td className="px-3 py-2.5 text-right align-top tabular-nums text-[var(--seo-text-light)]">
                      {row.words.toLocaleString()}
                    </td>
                    <td className="px-3 py-2.5 text-right align-top tabular-nums text-[var(--seo-text-light)]">
                      {row.images}
                      <span className="text-[var(--seo-muted)]"> / </span>
                      <span className={row.missingAlt > 0 ? "text-[var(--seo-error)]" : ""}>
                        {row.missingAlt}
                      </span>
                    </td>
                    <td className="px-3 py-2.5 text-right align-top tabular-nums text-[var(--seo-text-light)]">
                      {row.internal}
                      <span className="text-[var(--seo-muted)]"> / </span>
                      <span className={row.brokenInternal > 0 ? "text-[var(--seo-error)]" : ""}>
                        {row.brokenInternal}
                      </span>
                    </td>
                    <td className="px-3 py-2.5 text-right align-top tabular-nums">
                      <span className="inline-flex items-center justify-end gap-1.5">
                        {row.worstSev > 0 ? (
                          <span
                            className="inline-block h-2 w-2 shrink-0 rounded-full"
                            style={{ backgroundColor: severityColor(row.worstSevName).text }}
                            title={`Worst severity: ${row.worstSevName}`}
                          />
                        ) : null}
                        <span
                          className={row.issues > 0 ? "text-[var(--seo-text)]" : "text-[var(--seo-muted)]"}
                        >
                          {row.issues}
                        </span>
                      </span>
                    </td>
                  </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* Pagination */}
      {sorted.length > 0 ? (
        <div className="flex flex-wrap items-center justify-between gap-3 text-sm text-[var(--seo-text-light)]">
          <span>
            Showing {(start + 1).toLocaleString()}–
            {Math.min(start + PAGE_SIZE, sorted.length).toLocaleString()} of{" "}
            {sorted.length.toLocaleString()}
          </span>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              disabled={safePage <= 0}
              className="rounded-lg border border-[var(--seo-border-strong)] px-3 py-1.5 text-xs font-medium hover:bg-[var(--seo-card-hover)] disabled:cursor-not-allowed disabled:opacity-40"
            >
              Prev
            </button>
            <span className="tabular-nums">
              Page {safePage + 1} of {totalPages}
            </span>
            <button
              type="button"
              onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
              disabled={safePage >= totalPages - 1}
              className="rounded-lg border border-[var(--seo-border-strong)] px-3 py-1.5 text-xs font-medium hover:bg-[var(--seo-card-hover)] disabled:cursor-not-allowed disabled:opacity-40"
            >
              Next
            </button>
          </div>
        </div>
      ) : null}

      {/* Screaming-Frog-style lower detail pane: preview the selected URL's
          issues without leaving the grid. */}
      {selectedRow ? (
        <div ref={paneRef} className="scroll-mt-20">
          <DetailPane
            row={selectedRow}
            onClose={() => setSelectedRow(null)}
            onOpenFull={() => openDetail(selectedRow)}
          />
        </div>
      ) : null}
    </div>
  );
}

/** Inline detail pane rendered below the grid. Shows the clicked URL's header
 * (score/status/indexable/type + open-full/close actions), a quick-stats strip,
 * and the full issues list (each with its SeverityBadge + AffectedList), sorted
 * worst-severity first. Reuses the shared AffectedList "show where" component. */
function DetailPane({
  row,
  onClose,
  onOpenFull,
}: {
  row: Row;
  onClose: () => void;
  onOpenFull: () => void;
}) {
  // Sort a copy worst-severity first (critical → low); ties keep original order.
  const sortedIssues = useMemo<Issue[]>(() => {
    const issues = Array.isArray(row.r.all_issues) ? row.r.all_issues : [];
    return [...issues].sort(
      (a, b) =>
        (SEV_RANK[(b?.severity ?? "").toLowerCase()] ?? 0) -
        (SEV_RANK[(a?.severity ?? "").toLowerCase()] ?? 0),
    );
  }, [row.r.all_issues]);

  return (
    <Card className="mt-4 p-0">
      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-[var(--seo-border)] p-4">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <ScoreBadge score={row.score} />
            <span className={`text-xs font-semibold tabular-nums ${statusClass(row.status)}`}>
              {row.status ?? "—"}
            </span>
            <span className="pill bg-[var(--seo-card-hover)] text-[var(--seo-text-light)]">
              {row.type}
            </span>
            {row.indexable == null ? (
              <span className="text-xs text-[var(--seo-muted)]">Indexable —</span>
            ) : row.indexable ? (
              <span className="text-xs text-[var(--seo-success)]">Indexable</span>
            ) : (
              <span className="text-xs text-[var(--seo-error)]">Noindex</span>
            )}
          </div>
          {row.url ? (
            <a
              href={row.url}
              target="_blank"
              rel="noopener noreferrer"
              title={row.url}
              className="mt-1.5 block truncate text-sm font-medium text-[var(--seo-accent)] hover:underline"
            >
              {row.url}
            </a>
          ) : (
            <span className="mt-1.5 block text-sm text-[var(--seo-muted)]">—</span>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <button
            type="button"
            onClick={onOpenFull}
            className="rounded-lg btn-gradient px-3 py-1.5 text-xs font-semibold text-white"
          >
            Open full detail →
          </button>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close detail pane"
            className="shrink-0 rounded-lg p-1.5 text-[var(--seo-muted)] transition-colors hover:bg-[var(--seo-card-hover)] hover:text-[var(--seo-heading)]"
          >
            <XIcon size={16} />
          </button>
        </div>
      </div>

      {/* Quick stats */}
      <div className="grid grid-cols-2 gap-x-4 gap-y-3 border-b border-[var(--seo-border)] p-4 sm:grid-cols-4">
        <QuickStat label="Words" value={row.words.toLocaleString()} />
        <QuickStat
          label="Images / missing alt"
          value={
            <>
              {row.images.toLocaleString()}
              <span className="text-[var(--seo-muted)]"> / </span>
              <span className={row.missingAlt > 0 ? "text-[var(--seo-error)]" : ""}>
                {row.missingAlt.toLocaleString()}
              </span>
            </>
          }
        />
        <QuickStat
          label="Internal / broken"
          value={
            <>
              {row.internal.toLocaleString()}
              <span className="text-[var(--seo-muted)]"> / </span>
              <span className={row.brokenInternal > 0 ? "text-[var(--seo-error)]" : ""}>
                {row.brokenInternal.toLocaleString()}
              </span>
            </>
          }
        />
        <QuickStat label="Title length" value={`${row.titleLen} chars`} />
        <div className="col-span-2 sm:col-span-4">
          <div className="text-[11px] font-semibold uppercase tracking-wider text-[var(--seo-muted)]">
            Title
          </div>
          <div className="mt-1 text-sm text-[var(--seo-text)]">
            {row.title || <span className="text-[var(--seo-muted)]">—</span>}
          </div>
        </div>
      </div>

      {/* Issues */}
      <div className="p-4">
        <div className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-[var(--seo-muted)]">
          Issues ({sortedIssues.length})
        </div>
        {sortedIssues.length === 0 ? (
          <div className="rounded-lg border border-[var(--seo-border)] bg-[var(--seo-card-bg-alt)] px-3 py-4 text-center text-sm text-[var(--seo-muted)]">
            No issues found for this URL.
          </div>
        ) : (
          <ul className="flex flex-col gap-3">
            {sortedIssues.map((iss, i) => (
              <li
                key={`${iss.issue}-${i}`}
                className="rounded-lg border border-[var(--seo-border)] bg-[var(--seo-card-bg-alt)] p-3"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <SeverityBadge severity={iss.severity} />
                  {iss.category ? (
                    <span className="text-xs text-[var(--seo-muted)]">{iss.category}</span>
                  ) : null}
                </div>
                <div className="mt-1 text-sm font-medium text-[var(--seo-subheading)]">
                  {iss.issue}
                </div>
                {iss.recommendation ? (
                  <div className="mt-0.5 text-xs text-[var(--seo-text-light)]">
                    {iss.recommendation}
                  </div>
                ) : null}
                {Array.isArray(iss.affected) && iss.affected.length > 0 ? (
                  <AffectedList affected={iss.affected} baseUrl={row.url} />
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </div>
    </Card>
  );
}

/** One labelled figure in the detail pane's quick-stats strip. */
function QuickStat({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div>
      <div className="text-[11px] font-semibold uppercase tracking-wider text-[var(--seo-muted)]">
        {label}
      </div>
      <div className="mt-1 text-sm font-semibold text-[var(--seo-heading)] tabular-nums">
        {value}
      </div>
    </div>
  );
}

/** Toggle chip with a live count badge. `tone` tints the count badge (and the
 * active fill) so Errors/Warnings/Notices read at a glance; the default (no
 * tone) uses the neutral accent. */
function FilterChip({
  label,
  count,
  active,
  onClick,
  tone,
}: {
  label: string;
  count: number;
  active: boolean;
  onClick: () => void;
  tone?: "error" | "warning" | "notice";
}) {
  const toneColor =
    tone === "error"
      ? "var(--seo-error)"
      : tone === "warning"
        ? "var(--seo-warning)"
        : tone === "notice"
          ? "var(--seo-muted)"
          : "var(--seo-accent)";
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium transition-colors ${
        active
          ? "border text-white"
          : "border border-[var(--seo-border)] text-[var(--seo-text-light)] hover:bg-[var(--seo-card-hover)]"
      }`}
      style={active ? { backgroundColor: toneColor, borderColor: toneColor } : undefined}
    >
      <span>{label}</span>
      <span
        className="rounded-full px-1.5 py-0.5 text-[10px] font-semibold leading-none tabular-nums"
        style={
          active
            ? { backgroundColor: "rgba(255,255,255,0.22)", color: "#fff" }
            : { backgroundColor: "var(--seo-card-hover)", color: toneColor }
        }
      >
        {count.toLocaleString()}
      </span>
    </button>
  );
}

/** Clearly-labelled dropdown select, styled to match the Results page filters. */
function SelectFilter({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: { value: string; label: string }[];
}) {
  return (
    <div className="flex flex-col">
      <label className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-[var(--seo-muted)]">
        {label}
      </label>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="rounded-lg border border-[var(--seo-border)] bg-[var(--seo-card-bg)] px-3 py-1.5 text-sm text-[var(--seo-text)]"
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </div>
  );
}

function OverviewStat({ label, value }: { label: string; value: string }) {
  return (
    <Card>
      <div className="text-[11px] font-semibold uppercase tracking-wider text-[var(--seo-muted)]">
        {label}
      </div>
      <div className="mt-1 text-[22px] font-semibold leading-none tracking-tight text-[var(--seo-heading)] tabular-nums">
        {value}
      </div>
    </Card>
  );
}

function SortHeader({
  label,
  col,
  sortKey,
  sortDir,
  onSort,
  align = "left",
}: {
  label: string;
  col: SortKey;
  sortKey: SortKey;
  sortDir: SortDir;
  onSort: (key: SortKey) => void;
  align?: "left" | "right";
}) {
  const active = sortKey === col;
  return (
    <th className={`px-3 py-2.5 ${align === "right" ? "text-right" : "text-left"}`}>
      <button
        type="button"
        onClick={() => onSort(col)}
        className={`inline-flex items-center gap-1 uppercase tracking-wide transition-colors hover:text-[var(--seo-text)] ${
          active ? "text-[var(--seo-text)]" : ""
        } ${align === "right" ? "flex-row-reverse" : ""}`}
      >
        <span>{label}</span>
        <span className="text-[10px]">{active ? (sortDir === "asc" ? "▲" : "▼") : "↕"}</span>
      </button>
    </th>
  );
}
