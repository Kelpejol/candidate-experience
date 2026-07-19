/** Formatting helpers shared across screens. */

/**
 * The backend uses `datetime.utcnow()` — naive UTC serialized WITHOUT a
 * timezone suffix (e.g. "2026-07-17T14:32:05.123456"). Browsers parse a
 * tz-less string as LOCAL time, which would shift the displayed time. So we
 * append "Z" when no timezone is present, forcing correct UTC parsing.
 */
function parseBackendDate(iso: string): Date {
  const hasTz = /[zZ]$|[+-]\d{2}:?\d{2}$/.test(iso);
  return new Date(hasTz ? iso : `${iso}Z`);
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = parseBackendDate(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = parseBackendDate(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

/** "survey_sent" → "Survey Sent". For rendering enum values as labels. */
export function humanize(value: string): string {
  return value.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}
