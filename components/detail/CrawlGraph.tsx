"use client";

import { useMemo } from "react";

/** M5 T5.3: dependency-free node-link visualization of a crawl's internal link
 * graph (the top pages by Link Score + the internal edges among them).
 *
 * Layout is deterministic and cheap - no force simulation, no graph library:
 * nodes are laid out in columns by crawl depth (clicks from the homepage), left
 * to right, distributed vertically within each column. Node radius + color
 * encode Link Score. This reads as a "crawl tree" (structure by click-depth)
 * while still drawing the real cross-links between pages. */

export interface GraphNode {
  url: string;
  score: number;
  depth: number | null;
  inlinks: number | null;
  outlinks: number | null;
}
export interface GraphData {
  nodes: GraphNode[];
  edges: [number, number][];
  truncated: boolean;
  totalPages: number;
}

const W = 760;
const H = 420;
const PAD_X = 40;
const PAD_Y = 28;

function scoreColor(s: number): string {
  return s >= 60 ? "var(--seo-success)" : s >= 25 ? "var(--seo-warning)" : "var(--seo-error)";
}

export function CrawlGraph({ data }: { data: GraphData }) {
  const layout = useMemo(() => {
    const nodes = data.nodes;
    if (nodes.length === 0) return null;
    // Column per depth (unknown depth -> its own trailing column).
    const depths = nodes.map((n) => (typeof n.depth === "number" ? n.depth : 99));
    const uniqueDepths = [...new Set(depths)].sort((a, b) => a - b);
    const colX = new Map<number, number>();
    const cols = uniqueDepths.length;
    uniqueDepths.forEach((d, i) => {
      colX.set(d, cols === 1 ? W / 2 : PAD_X + (i * (W - 2 * PAD_X)) / (cols - 1));
    });
    // Vertical slot within each column.
    const perCol = new Map<number, number>();
    const colCount = new Map<number, number>();
    depths.forEach((d) => colCount.set(d, (colCount.get(d) || 0) + 1));

    const pos = nodes.map((n, i) => {
      const d = depths[i];
      const idx = perCol.get(d) || 0;
      perCol.set(d, idx + 1);
      const total = colCount.get(d) || 1;
      const y = total === 1 ? H / 2 : PAD_Y + (idx * (H - 2 * PAD_Y)) / (total - 1);
      const r = 4 + Math.round((n.score / 100) * 9); // 4..13px by score
      return { x: colX.get(d)!, y, r, node: n };
    });
    return { pos, uniqueDepths, colX };
  }, [data]);

  if (!layout) return null;
  const { pos, uniqueDepths, colX } = layout;

  return (
    <div className="overflow-x-auto">
      <svg viewBox={`0 0 ${W} ${H + 24}`} className="w-full" style={{ minWidth: 520 }} role="img"
           aria-label="Internal link graph: top pages by Link Score, laid out by crawl depth">
        {/* depth axis labels */}
        {uniqueDepths.map((d) => (
          <text key={`lbl-${d}`} x={colX.get(d)} y={H + 16} textAnchor="middle"
                fontSize="10" fill="var(--seo-muted)">
            {d === 99 ? "?" : `Depth ${d}`}
          </text>
        ))}
        {/* edges */}
        <g stroke="var(--seo-border-strong)" strokeOpacity="0.5" strokeWidth="1" fill="none">
          {data.edges.map(([i, j], k) => {
            const a = pos[i], b = pos[j];
            if (!a || !b) return null;
            return <line key={k} x1={a.x} y1={a.y} x2={b.x} y2={b.y} />;
          })}
        </g>
        {/* nodes */}
        <g>
          {pos.map((p, i) => (
            <circle key={i} cx={p.x} cy={p.y} r={p.r} fill={scoreColor(p.node.score)}
                    fillOpacity="0.85" stroke="var(--seo-card-bg)" strokeWidth="1">
              <title>{`${p.node.url}\nLink Score ${p.node.score} · depth ${p.node.depth ?? "?"} · in ${p.node.inlinks ?? "?"} / out ${p.node.outlinks ?? "?"}`}</title>
            </circle>
          ))}
        </g>
      </svg>
      <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-[var(--seo-muted)]">
        <span>Bigger / greener = higher internal Link Score. Columns = clicks from the homepage.</span>
        <span className="flex items-center gap-1"><span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: "var(--seo-success)" }} /> ≥60</span>
        <span className="flex items-center gap-1"><span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: "var(--seo-warning)" }} /> 25–59</span>
        <span className="flex items-center gap-1"><span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: "var(--seo-error)" }} /> &lt;25</span>
        {data.truncated ? <span>Showing the top {data.nodes.length} of {data.totalPages.toLocaleString()} pages by Link Score.</span> : null}
      </div>
    </div>
  );
}
