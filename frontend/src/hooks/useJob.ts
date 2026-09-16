import { useQuery } from "@tanstack/react-query";

import { getJobStatus } from "../api/jobs";

/**
 * RQ job states that mean "done — stop polling". Anything else (`queued`,
 * `started`, `deferred`, `scheduled`) is still in flight.
 */
export const TERMINAL_JOB_STATUSES = new Set([
  "finished",
  "failed",
  "stopped",
  "canceled",
]);

/**
 * Poll a background job until it reaches a terminal state.
 *
 * Usage: enqueue something that returns `{ job_id }` (a 202 endpoint), store
 * that id in state, and pass it here. The hook polls GET /jobs/{id} every
 * 1.5s and automatically stops once the job finishes or fails.
 *
 *   const [jobId, setJobId] = useState<string | null>(null);
 *   const job = useJob(jobId);
 *   // job.isPolling, job.isFinished, job.isFailed, job.data?.result
 *
 * Pass `null` to disable (nothing polls until you have a job id).
 */
export function useJob(jobId: string | null | undefined) {
  const query = useQuery({
    queryKey: ["job", jobId],
    queryFn: () => getJobStatus(jobId as string),
    enabled: Boolean(jobId),
    refetchInterval: (query) => {
      // Stop on error too: once RQ's result TTL lapses the job id 404s
      // forever, and polling on would spin a 1.5s request loop and leave the
      // caller's button disabled for the rest of the session.
      if (query.state.status === "error") return false;
      const status = query.state.data?.status;
      if (status && TERMINAL_JOB_STATUSES.has(status)) return false;
      return 1500;
    },
    retry: false,
  });

  const status = query.data?.status;
  const isTerminal = Boolean(status && TERMINAL_JOB_STATUSES.has(status));

  return {
    ...query,
    status,
    isTerminal,
    isFinished: status === "finished",
    isFailed: status === "failed",
    /** True while the job is enqueued, not yet terminal, and still reachable. */
    isPolling: Boolean(jobId) && !isTerminal && !query.isError,
  };
}
