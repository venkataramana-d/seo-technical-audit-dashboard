"use client";

import { useEffect, useState } from "react";

// Reports whether a server-side Groq/PSI key is configured (single GET), so the
// Settings page can show the right "configured" state without re-deriving it.
export function useAiConfigStatus() {
  const [psiConfigured, setPsiConfigured] = useState<boolean | null>(null);
  const [groqConfigured, setGroqConfigured] = useState<boolean | null>(null);

  useEffect(() => {
    fetch("/api/ai")
      .then((r) => r.json())
      .then((d) => {
        setPsiConfigured(Boolean(d.psiConfigured));
        setGroqConfigured(Boolean(d.groqConfigured));
      })
      .catch(() => {
        setPsiConfigured(false);
        setGroqConfigured(false);
      });
  }, []);

  return { psiConfigured, groqConfigured };
}
