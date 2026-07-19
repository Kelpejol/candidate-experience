import { api } from "../lib/apiClient";
import type { HealthResponse } from "../lib/types";

export function getHealth() {
  return api.get<HealthResponse>("/health");
}
