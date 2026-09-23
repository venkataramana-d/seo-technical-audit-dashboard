"use client";

import { useState } from "react";
import { Card } from "@/components/ui";
import { useAudit } from "@/lib/state/AuditContext";
import type { Issue } from "@/lib/types";

/**
 * "Ask your crawl" (M4 T4.2): a natural-language Q&A grounded in the audit's
 * issue data. Sends the same deduplicated issue digest the AI Summary uses plus
 * the user's question to Groq (api/ai.py action "ask"); the server answers ONLY
 * from that data (no hallucinated numbers). Not cached - each question is a
 * fresh, cheap single call.
 */
const SUGGESTED = [
  "What are the most urgent issues to fix first?",
  "Which problems affect the most pages?",
  "How is my technical SEO overall?",
];

export function AskCrawlCard({
  url,
  seoScore,
  issues,
  contextLabel,
  className = "",
}: {
  url?: string;
  seoScore: number;
  issues: Issue[];
  contextLabel?: string;
  className?: string;
}) {
  const { groqApiKey } = useAudit();
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function ask(q: string) {
    const query = q.trim();
    if (!query || loading) return;
    setLoading(true);
    setError(null);
    setAnswer(null);
    try {
      const res = await fetch("/api/ai", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action: "ask",
          question: query,
          url: url || "",
          seoScore,
          allIssues: issues,
          contextLabel,
          apiKey: groqApiKey || undefined,
        }),
      });
      const data = await res.json();
      if (data.ok) setAnswer(data.answer);
      else setError(data.error || "The assistant couldn't answer.");
    } catch {
      setError("Request failed.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <Card className={className}>
      <h3 className="mb-1 text-sm font-semibold text-[var(--seo-subheading)]">Ask your audit</h3>
      <p className="mb-3 text-xs text-[var(--seo-muted)]">
        Ask a question about these results in plain English - answered only from your audit data (no invented numbers).
      </p>
      <form
        onSubmit={(e) => { e.preventDefault(); ask(question); }}
        className="flex flex-wrap gap-2"
      >
        <input
          type="text"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="e.g. Which issues should I fix first?"
          className="min-w-0 flex-1 rounded-lg border border-[var(--seo-border-strong)] bg-[var(--seo-card-bg)] px-3 py-1.5 text-sm text-[var(--seo-text)] outline-none focus:border-[var(--seo-accent)]"
        />
        <button
          type="submit"
          disabled={loading || !question.trim()}
          className="rounded-lg btn-gradient px-3 py-1.5 text-sm font-semibold text-white disabled:opacity-50"
        >
          {loading ? "Asking…" : "Ask"}
        </button>
      </form>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {SUGGESTED.map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => { setQuestion(s); ask(s); }}
            disabled={loading}
            className="rounded-full border border-[var(--seo-border)] px-2.5 py-1 text-xs text-[var(--seo-text-light)] hover:bg-[var(--seo-card-hover)] disabled:opacity-50"
          >
            {s}
          </button>
        ))}
      </div>
      {error ? <p className="mt-3 text-sm text-[var(--seo-error)]">{error}</p> : null}
      {answer ? (
        <p className="mt-3 whitespace-pre-wrap text-sm text-[var(--seo-text)]">{answer}</p>
      ) : null}
    </Card>
  );
}
