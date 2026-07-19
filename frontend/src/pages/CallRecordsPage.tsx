import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { listCallRecords } from "../api/callRecords";
import type { ListCallRecordsParams } from "../api/callRecords";
import { CallRecordDetailModal } from "../components/callRecords/CallRecordDetailModal";
import {
  CallDispositionBadge,
  DirectionBadge,
} from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { inputClass } from "../components/ui/FormField";
import { EmptyState, ErrorState, LoadingState } from "../components/ui/QueryStates";
import { formatDateTime, humanize } from "../lib/format";
import {
  ASSESSMENT_TOOLS,
  CALL_DIRECTIONS,
  CALL_DISPOSITIONS,
} from "../lib/types";
import type { CallDirection, CallDisposition } from "../lib/types";

const PAGE_SIZE = 50;

export function CallRecordsPage() {
  const [direction, setDirection] = useState<CallDirection | "">("");
  const [disposition, setDisposition] = useState<CallDisposition | "">("");
  const [toolName, setToolName] = useState("");
  const [campaignName, setCampaignName] = useState("");
  const [offset, setOffset] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const params: ListCallRecordsParams = {
    direction: direction || undefined,
    disposition: disposition || undefined,
    tool_name: toolName || undefined,
    campaign_name: campaignName.trim() || undefined,
    limit: PAGE_SIZE,
    offset,
  };

  const records = useQuery({
    queryKey: ["call-records", params],
    queryFn: () => listCallRecords(params),
  });

  // Any filter change returns to the first page.
  function resetToFirstPage() {
    setOffset(0);
  }

  const rows = records.data ?? [];
  const canPrev = offset > 0;
  const canNext = rows.length === PAGE_SIZE;

  return (
    <div className="mx-auto max-w-5xl">
      <header className="mb-6">
        <h2 className="text-xl font-semibold text-slate-900">Call Records</h2>
        <p className="mt-1 text-sm text-slate-600">
          Every inbound and outbound voice call handled by the Calling Agent.
        </p>
      </header>

      {/* Filters */}
      <Card className="mb-4 flex flex-wrap gap-3 p-4">
        <select
          className={`${inputClass} w-auto`}
          value={direction}
          onChange={(e) => {
            setDirection(e.target.value as CallDirection | "");
            resetToFirstPage();
          }}
        >
          <option value="">All directions</option>
          {CALL_DIRECTIONS.map((d) => (
            <option key={d} value={d}>
              {humanize(d)}
            </option>
          ))}
        </select>

        <select
          className={`${inputClass} w-auto`}
          value={disposition}
          onChange={(e) => {
            setDisposition(e.target.value as CallDisposition | "");
            resetToFirstPage();
          }}
        >
          <option value="">All dispositions</option>
          {CALL_DISPOSITIONS.map((d) => (
            <option key={d} value={d}>
              {humanize(d)}
            </option>
          ))}
        </select>

        <select
          className={`${inputClass} w-auto`}
          value={toolName}
          onChange={(e) => {
            setToolName(e.target.value);
            resetToFirstPage();
          }}
        >
          <option value="">All tools</option>
          {ASSESSMENT_TOOLS.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>

        <input
          className={`${inputClass} w-auto flex-1`}
          placeholder="Filter by campaign name…"
          value={campaignName}
          onChange={(e) => {
            setCampaignName(e.target.value);
            resetToFirstPage();
          }}
        />
      </Card>

      {records.isLoading && <LoadingState label="Loading call records…" />}
      {records.isError && (
        <ErrorState error={records.error} onRetry={() => records.refetch()} />
      )}

      {records.data && rows.length === 0 && (
        <EmptyState
          title="No call records"
          description="No calls match these filters yet."
        />
      )}

      {rows.length > 0 && (
        <Card className="overflow-hidden">
          <table className="w-full text-sm">
            <thead className="border-b border-slate-200 bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-4 py-3 font-medium">Direction</th>
                <th className="px-4 py-3 font-medium">Candidate</th>
                <th className="px-4 py-3 font-medium">Tool</th>
                <th className="px-4 py-3 font-medium">Campaign</th>
                <th className="px-4 py-3 font-medium">Disposition</th>
                <th className="px-4 py-3 font-medium">When</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {rows.map((record) => (
                <tr
                  key={record.id}
                  onClick={() => setSelectedId(record.external_call_id)}
                  className="cursor-pointer hover:bg-slate-50"
                >
                  <td className="px-4 py-3">
                    <DirectionBadge direction={record.direction} />
                  </td>
                  <td className="px-4 py-3 font-medium text-slate-900">
                    {record.candidate_name ?? record.candidate_phone ?? "—"}
                  </td>
                  <td className="px-4 py-3 text-slate-600">
                    {record.tool_name ?? "—"}
                  </td>
                  <td className="px-4 py-3 text-slate-600">
                    {record.campaign_name ?? "—"}
                  </td>
                  <td className="px-4 py-3">
                    <CallDispositionBadge disposition={record.disposition} />
                  </td>
                  <td className="px-4 py-3 text-slate-500">
                    {formatDateTime(record.created_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      {(canPrev || canNext) && (
        <div className="mt-4 flex items-center justify-between text-sm text-slate-500">
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

      <CallRecordDetailModal
        externalCallId={selectedId}
        open={Boolean(selectedId)}
        onClose={() => setSelectedId(null)}
      />
    </div>
  );
}
