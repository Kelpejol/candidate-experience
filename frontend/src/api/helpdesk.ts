import { api } from "../lib/apiClient";
import type { HelpdeskSummary } from "../lib/types";

/** Automation health over the Helpdesk AI decision audit trail. */
export function getHelpdeskSummary() {
  return api.get<HelpdeskSummary>("/helpdesk/reports/summary");
}
