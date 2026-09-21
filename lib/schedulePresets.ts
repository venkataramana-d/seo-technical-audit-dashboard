/** Common recurring-crawl cadences, mapped to concrete cron expressions -
 * shared between the "Start New Crawl" form and the crawl detail page's
 * Schedule card so both present/parse the same presets consistently.
 * Hand-writing cron syntax is real friction for a first pass; "Custom…"
 * still exposes the raw expression for anyone who wants it. */
export interface SchedulePreset {
  id: string;
  label: string;
  cron: string | null; // null = "Off"; undefined-like sentinel for "Custom" is handled by id, not cron
}

export const SCHEDULE_PRESETS: SchedulePreset[] = [
  { id: "off", label: "Off", cron: null },
  { id: "6h", label: "Every 6 hours", cron: "0 */6 * * *" },
  { id: "daily", label: "Daily at midnight", cron: "0 0 * * *" },
  { id: "weekly", label: "Weekly (Sundays at midnight)", cron: "0 0 * * 0" },
  { id: "custom", label: "Custom…", cron: null },
];

/** Given a saved cron expression (or null/undefined for unscheduled), finds
 * the matching preset id, falling back to "custom" for anything hand-written
 * that doesn't match one of the standard cadences. */
export function presetIdForCron(cron: string | null | undefined): string {
  if (!cron) return "off";
  const match = SCHEDULE_PRESETS.find((p) => p.id !== "custom" && p.cron === cron);
  return match ? match.id : "custom";
}

/** Human-readable label for a saved cron expression. */
export function humanizeCron(cron: string | null | undefined): string {
  if (!cron) return "Not scheduled";
  const match = SCHEDULE_PRESETS.find((p) => p.id !== "custom" && p.cron === cron);
  return match ? match.label : cron;
}
