import { api } from "../lib/apiClient";
import type { HelpdeskAIActionRead, HelpdeskSummary } from "../lib/types";

/** Automation health over the Helpdesk AI decision audit trail. */
export function getHelpdeskSummary() {
  return api.get<HelpdeskSummary>("/helpdesk/reports/summary");
}

/** Recent per-ticket AI decisions (newest first) for the review view. */
export function getHelpdeskAIActions(params?: {
  action_type?: string;
  only_drafts?: boolean;
  limit?: number;
}) {
  const q = new URLSearchParams();
  if (params?.action_type) q.set("action_type", params.action_type);
  if (params?.only_drafts) q.set("only_drafts", "true");
  if (params?.limit) q.set("limit", String(params.limit));
  const qs = q.toString();
  return api.get<HelpdeskAIActionRead[]>(
    `/helpdesk/reports/actions${qs ? `?${qs}` : ""}`,
  );
}
