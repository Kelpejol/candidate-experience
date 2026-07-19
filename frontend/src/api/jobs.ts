import { api } from "../lib/apiClient";
import type { JobStatusRead } from "../lib/types";

/**
 * Poll a background job's status. Returns 404 (ApiError) once the job's
 * result has expired from Redis. Drive this with the `useJob` hook rather
 * than calling it in a manual loop.
 */
export function getJobStatus(jobId: string) {
  return api.get<JobStatusRead>(`/jobs/${jobId}`);
}
