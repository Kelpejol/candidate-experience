import { useQuery } from "@tanstack/react-query";

import { getCallRecord } from "../../api/callRecords";
import { formatDateTime } from "../../lib/format";
import { CallDispositionBadge, DirectionBadge } from "../ui/Badge";
import { Modal } from "../ui/Modal";
import { ErrorState, LoadingState } from "../ui/QueryStates";

function Field({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-slate-400">{label}</dt>
      <dd className="mt-0.5 text-sm text-slate-800">{value || "—"}</dd>
    </div>
  );
}

/**
 * Fetches the full record by external_call_id (GET /call-records/{id}) — so
 * this modal also works as a deep-link target, not just from the list row.
 */
export function CallRecordDetailModal({
  externalCallId,
  open,
  onClose,
}: {
  externalCallId: string | null;
  open: boolean;
  onClose: () => void;
}) {
  const record = useQuery({
    queryKey: ["call-record", externalCallId],
    queryFn: () => getCallRecord(externalCallId as string),
    enabled: open && Boolean(externalCallId),
  });

  return (
    <Modal open={open} onClose={onClose} title="Call record">
      {record.isLoading && <LoadingState label="Loading record…" />}
      {record.isError && <ErrorState error={record.error} />}
      {record.data && (
        <div className="space-y-5">
          <div className="flex flex-wrap items-center gap-2">
            <DirectionBadge direction={record.data.direction} />
            <CallDispositionBadge disposition={record.data.disposition} />
          </div>

          <dl className="grid grid-cols-2 gap-x-6 gap-y-4">
            <Field label="External call ID" value={record.data.external_call_id} />
            <Field label="Candidate" value={record.data.candidate_name} />
            <Field label="Phone" value={record.data.candidate_phone} />
            <Field label="Tool" value={record.data.tool_name} />
            <Field label="Campaign" value={record.data.campaign_name} />
            <Field
              label="Started"
              value={formatDateTime(record.data.call_start_time)}
            />
            <Field
              label="Ended"
              value={formatDateTime(record.data.call_end_time)}
            />
            <Field label="Created" value={formatDateTime(record.data.created_at)} />
          </dl>

          {record.data.issue_summary && (
            <Field label="Issue summary" value={record.data.issue_summary} />
          )}

          {record.data.transcription && (
            <div>
              <dt className="text-xs uppercase tracking-wide text-slate-400">
                Transcript
              </dt>
              <dd className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap rounded-md border border-slate-200 bg-slate-50 p-3 text-sm text-slate-700">
                {record.data.transcription}
              </dd>
            </div>
          )}

          {record.data.recording_url && (
            <Field
              label="Recording"
              value={
                <a
                  href={record.data.recording_url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-blue-600 underline"
                >
                  Open recording
                </a>
              }
            />
          )}

          {/* Handoff leg (only when the call was escalated to a human) */}
          {record.data.handoff_status && (
            <div className="border-t border-slate-100 pt-4">
              <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
                Human handoff
              </p>
              <dl className="grid grid-cols-2 gap-x-6 gap-y-4">
                <Field label="Status" value={record.data.handoff_status} />
                <Field label="Call SID" value={record.data.handoff_call_sid} />
                <Field
                  label="Duration"
                  value={
                    record.data.handoff_duration != null
                      ? `${record.data.handoff_duration}s`
                      : null
                  }
                />
                <Field
                  label="Bridged"
                  value={
                    record.data.handoff_bridged == null
                      ? null
                      : record.data.handoff_bridged
                        ? "Yes"
                        : "No"
                  }
                />
              </dl>
            </div>
          )}
        </div>
      )}
    </Modal>
  );
}
