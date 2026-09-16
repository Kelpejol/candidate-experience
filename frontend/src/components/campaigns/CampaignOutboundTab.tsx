import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  buildOutboundQueue,
  buildOutboundRetryQueue,
  enqueueExecuteNextOutboundJob,
  getNextOutboundAttempt,
  listOutboundAnswers,
  listOutboundAttempts,
  updateOutboundAttemptStatus,
} from "../../api/campaigns";
import { CampaignOutboundSettingsCard } from "./CampaignOutboundSettingsCard";
import { useJob } from "../../hooks/useJob";
import { ApiError } from "../../lib/apiClient";
import { formatDateTime, humanize } from "../../lib/format";
import { OUTBOUND_ATTEMPT_STATUSES } from "../../lib/types";
import type {
  OutboundCallAttemptRead,
  OutboundCallAttemptStatus,
  OutboundSurveyAnswerRead,
} from "../../lib/types";
import { AttemptStatusBadge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { Modal } from "../ui/Modal";
import { EmptyState, ErrorState, LoadingState } from "../ui/QueryStates";
import { Spinner } from "../ui/Spinner";

const PAGE_SIZE = 50;

export function CampaignOutboundTab({ campaignId }: { campaignId: string }) {
  const queryClient = useQueryClient();
  const [offset, setOffset] = useState(0);
  const [maxAttempts, setMaxAttempts] = useState(3);
  const [executeJobId, setExecuteJobId] = useState<string | null>(null);
  const [confirmCallOpen, setConfirmCallOpen] = useState(false);

  function invalidateAll() {
    queryClient.invalidateQueries({ queryKey: ["outbound-attempts", campaignId] });
    queryClient.invalidateQueries({ queryKey: ["outbound-next", campaignId] });
    queryClient.invalidateQueries({ queryKey: ["campaign-summary", campaignId] });
    // The collected answers change as calls complete — without this the
    // responses panel keeps showing "no responses yet" after a finished call.
    queryClient.invalidateQueries({ queryKey: ["outbound-answers", campaignId] });
  }

  const attempts = useQuery({
    queryKey: ["outbound-attempts", campaignId, offset],
    queryFn: () => listOutboundAttempts(campaignId, PAGE_SIZE, offset),
  });

  const nextQueued = useQuery({
    queryKey: ["outbound-next", campaignId],
    queryFn: () => getNextOutboundAttempt(campaignId),
    retry: false, // a 404 (nothing queued) is an expected outcome, not a failure
  });
  const noneQueued =
    nextQueued.isError &&
    nextQueued.error instanceof ApiError &&
    nextQueued.error.status === 404;

  const buildQueue = useMutation({
    mutationFn: () => buildOutboundQueue(campaignId),
    onSuccess: invalidateAll,
  });
  const retryQueue = useMutation({
    mutationFn: () => buildOutboundRetryQueue(campaignId, maxAttempts),
    onSuccess: invalidateAll,
  });
  const executeNext = useMutation({
    mutationFn: () => enqueueExecuteNextOutboundJob(campaignId),
    onSuccess: (res) => setExecuteJobId(res.job_id),
  });

  const job = useJob(executeJobId);
  useEffect(() => {
    if (job.isFinished) invalidateAll();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job.isFinished]);

  const rows = attempts.data ?? [];
  const canPrev = offset > 0;
  const canNext = rows.length === PAGE_SIZE;

  return (
    <div className="space-y-4">
      {/* Call context — what the agent says (editable) */}
      <CampaignOutboundSettingsCard campaignId={campaignId} />

      {/* Actions */}
      <Card className="flex flex-wrap items-end gap-4 p-5">
        <Button
          onClick={() => buildQueue.mutate()}
          disabled={buildQueue.isPending}
        >
          {buildQueue.isPending ? <Spinner /> : "Build queue"}
        </Button>

        <div className="flex items-end gap-2">
          <label className="text-sm">
            <span className="mb-1 block text-slate-600">Max attempts</span>
            <input
              type="number"
              min={1}
              max={10}
              value={maxAttempts}
              // Clamp here: this input isn't in a <form>, so the browser never
              // enforces min/max, and an empty box (Number("") === 0) would be
              // sent as max_attempts=0 and rejected by the API with a raw 422.
              onChange={(e) =>
                setMaxAttempts(
                  Math.min(10, Math.max(1, Number(e.target.value) || 1)),
                )
              }
              className="w-20 rounded-md border border-slate-300 px-2 py-1.5 text-sm"
            />
          </label>
          <Button
            variant="secondary"
            onClick={() => retryQueue.mutate()}
            disabled={retryQueue.isPending}
          >
            {retryQueue.isPending ? <Spinner /> : "Build retry queue"}
          </Button>
        </div>

        <div className="ml-auto">
          <Button
            onClick={() => setConfirmCallOpen(true)}
            disabled={executeNext.isPending || job.isPolling || noneQueued}
          >
            {executeNext.isPending || job.isPolling ? (
              <Spinner />
            ) : (
              "Execute next call"
            )}
          </Button>
        </div>
      </Card>

      {/* Placing a real call is irreversible — name who gets dialled. */}
      <Modal
        open={confirmCallOpen}
        onClose={() => setConfirmCallOpen(false)}
        title="Place this call?"
      >
        <p className="text-sm text-slate-600">
          This dials{" "}
          <span className="font-medium text-slate-900">
            {nextQueued.data?.candidate_name ?? "the next queued candidate"}
          </span>{" "}
          on{" "}
          <span className="font-medium text-slate-900">
            {nextQueued.data?.phone ?? "their number"}
          </span>{" "}
          right now (attempt {nextQueued.data?.attempt_number ?? "?"}). A real
          phone call cannot be undone.
        </p>
        <div className="mt-5 flex justify-end gap-2">
          <Button variant="secondary" onClick={() => setConfirmCallOpen(false)}>
            Cancel
          </Button>
          <Button
            variant="danger"
            onClick={() => {
              setConfirmCallOpen(false);
              executeNext.mutate();
            }}
            disabled={executeNext.isPending}
          >
            Yes, place the call
          </Button>
        </div>
      </Modal>

      {(buildQueue.isError || retryQueue.isError || executeNext.isError) && (
        <ErrorState
          error={buildQueue.error || retryQueue.error || executeNext.error}
        />
      )}

      {/* Next queued + execute job status */}
      <Card className="p-4 text-sm">
        <span className="text-slate-500">Next queued: </span>
        {nextQueued.isLoading ? (
          "…"
        ) : noneQueued ? (
          <span className="text-slate-400">none queued</span>
        ) : nextQueued.isError ? (
          // A backend/network failure must not read as "queue is empty".
          <span className="text-red-600">
            couldn’t load — {(nextQueued.error as Error).message}
          </span>
        ) : nextQueued.data ? (
          <span className="font-medium text-slate-800">
            {nextQueued.data.candidate_name ?? nextQueued.data.phone} (attempt{" "}
            {nextQueued.data.attempt_number})
          </span>
        ) : (
          "—"
        )}

        {executeJobId && (
          <div className="mt-2 text-slate-600">
            Execute job <code className="text-xs">{executeJobId}</code> ·{" "}
            <span className="font-medium">
              {job.status ? humanize(job.status) : "Starting…"}
            </span>
            {job.isPolling && <Spinner className="ml-2 text-slate-400" />}
            {job.isFailed && (
              <span className="ml-2 text-red-600">{job.data?.error}</span>
            )}
            {job.isError && (
              // The poll itself failed (e.g. the job result expired).
              <span className="ml-2 text-red-600">
                lost track of this job — {(job.error as Error).message}
              </span>
            )}
          </div>
        )}
      </Card>

      {/* Attempts table */}
      {attempts.isLoading && <LoadingState label="Loading attempts…" />}
      {attempts.isError && (
        <ErrorState error={attempts.error} onRetry={() => attempts.refetch()} />
      )}
      {attempts.data && rows.length === 0 && (
        <EmptyState
          title="No outbound attempts yet"
          description="Build the queue to create call attempts for eligible non-responders."
        />
      )}
      {rows.length > 0 && (
        <Card className="overflow-hidden">
          <table className="w-full text-sm">
            <thead className="border-b border-slate-200 bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-4 py-3 font-medium">Candidate</th>
                <th className="px-4 py-3 font-medium">Phone</th>
                <th className="px-4 py-3 font-medium">#</th>
                <th className="px-4 py-3 font-medium">Status</th>
                <th className="px-4 py-3 font-medium">Started</th>
                <th className="px-4 py-3 font-medium">
                  Set outcome{" "}
                  <span className="normal-case text-slate-400">(test)</span>
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {rows.map((attempt) => (
                <tr key={attempt.id} className="hover:bg-slate-50">
                  <td className="px-4 py-3 font-medium text-slate-900">
                    {attempt.candidate_name ?? "—"}
                  </td>
                  <td className="px-4 py-3 text-slate-600">{attempt.phone}</td>
                  <td className="px-4 py-3 text-slate-600">
                    {attempt.attempt_number}
                  </td>
                  <td className="px-4 py-3">
                    <AttemptStatusBadge status={attempt.status} />
                  </td>
                  <td className="px-4 py-3 text-slate-500">
                    {formatDateTime(attempt.started_at)}
                  </td>
                  <td className="px-4 py-3">
                    <AttemptStatusEditor
                      campaignId={campaignId}
                      attempt={attempt}
                      onUpdated={invalidateAll}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      {/* Survey responses collected by voice */}
      <OutboundResponses campaignId={campaignId} />

      {(canPrev || canNext) && (
        <div className="flex items-center justify-between text-sm text-slate-500">
          <span>
            Showing {offset + 1}–{offset + rows.length}
          </span>
          <div className="flex gap-2">
            <Button
              variant="secondary"
              size="sm"
              disabled={!canPrev}
              onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}
            >
              Previous
            </Button>
            <Button
              variant="secondary"
              size="sm"
              disabled={!canNext}
              onClick={() => setOffset((o) => o + PAGE_SIZE)}
            >
              Next
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * Per-row status setter. In production the telephony provider PATCHes an
 * attempt's status via callback; this lets you simulate that outcome from the
 * UI to exercise the pipeline without placing a real call.
 */
function AttemptStatusEditor({
  campaignId,
  attempt,
  onUpdated,
}: {
  campaignId: string;
  attempt: OutboundCallAttemptRead;
  onUpdated: () => void;
}) {
  const mutation = useMutation({
    mutationFn: (status: OutboundCallAttemptStatus) =>
      updateOutboundAttemptStatus(campaignId, attempt.id, { status }),
    onSuccess: onUpdated,
  });

  return (
    <div className="flex items-center gap-2">
      <select
        className="rounded-md border border-slate-300 px-2 py-1 text-xs"
        value={attempt.status}
        disabled={mutation.isPending}
        onChange={(e) =>
          mutation.mutate(e.target.value as OutboundCallAttemptStatus)
        }
      >
        {OUTBOUND_ATTEMPT_STATUSES.map((status) => (
          <option key={status} value={status}>
            {humanize(status)}
          </option>
        ))}
      </select>
      {mutation.isPending && <Spinner className="text-slate-400" />}
    </div>
  );
}

/**
 * The survey answers collected by voice on outbound calls, grouped per
 * candidate (question -> answer). This is the payoff of the outbound survey
 * agent — the feedback it gathered from non-responders.
 */
function OutboundResponses({ campaignId }: { campaignId: string }) {
  const answers = useQuery({
    queryKey: ["outbound-answers", campaignId],
    queryFn: () => listOutboundAnswers(campaignId),
  });

  if (answers.isLoading) return <LoadingState label="Loading responses…" />;
  if (answers.isError)
    return (
      <ErrorState error={answers.error} onRetry={() => answers.refetch()} />
    );

  const rows = answers.data ?? [];
  if (rows.length === 0) {
    return (
      <EmptyState
        title="No survey responses yet"
        description="Answers candidates give on outbound calls will appear here."
      />
    );
  }

  // Group by candidate, preserving first-seen order; order each group's
  // answers by question position.
  const groups = new Map<
    string,
    { name: string; items: OutboundSurveyAnswerRead[] }
  >();
  for (const a of rows) {
    if (!groups.has(a.candidate_id))
      groups.set(a.candidate_id, {
        name: a.candidate_name ?? "Unknown candidate",
        items: [],
      });
    groups.get(a.candidate_id)!.items.push(a);
  }
  for (const g of groups.values())
    g.items.sort((x, y) => (x.position ?? 0) - (y.position ?? 0));

  return (
    <div className="space-y-3">
      <h3 className="text-sm font-semibold text-slate-900">
        Survey responses{" "}
        <span className="font-normal text-slate-400">({groups.size})</span>
      </h3>
      {[...groups.entries()].map(([candidateId, g]) => (
        <Card key={candidateId} className="p-4">
          <p className="mb-3 text-sm font-medium text-slate-900">{g.name}</p>
          <dl className="space-y-2">
            {g.items.map((a) => (
              <div
                key={a.id}
                className="grid grid-cols-1 gap-0.5 sm:grid-cols-3 sm:gap-3"
              >
                <dt className="text-sm text-slate-500 sm:col-span-2">
                  {a.question}
                </dt>
                <dd className="text-sm font-medium text-slate-900">
                  {a.answer}
                </dd>
              </div>
            ))}
          </dl>
        </Card>
      ))}
    </div>
  );
}
