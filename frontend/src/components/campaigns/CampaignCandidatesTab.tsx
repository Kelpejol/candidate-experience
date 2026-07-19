import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { listCandidates, listNonResponders } from "../../api/campaigns";
import { CallStatusBadge, SurveyStatusBadge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { EmptyState, ErrorState, LoadingState } from "../ui/QueryStates";
import { AddCandidateModal } from "./AddCandidateModal";
import { UploadCandidatesModal } from "./UploadCandidatesModal";

const PAGE_SIZE = 50;

export function CampaignCandidatesTab({ campaignId }: { campaignId: string }) {
  const queryClient = useQueryClient();
  const [offset, setOffset] = useState(0);
  const [nonRespondersOnly, setNonRespondersOnly] = useState(false);
  const [addOpen, setAddOpen] = useState(false);
  const [uploadOpen, setUploadOpen] = useState(false);

  const candidates = useQuery({
    queryKey: ["candidates", campaignId, nonRespondersOnly, offset],
    queryFn: () =>
      nonRespondersOnly
        ? listNonResponders(campaignId, PAGE_SIZE, offset)
        : listCandidates(campaignId, PAGE_SIZE, offset),
  });

  function invalidateData() {
    queryClient.invalidateQueries({ queryKey: ["candidates", campaignId] });
    queryClient.invalidateQueries({ queryKey: ["campaign-summary", campaignId] });
  }

  function toggleNonResponders() {
    setNonRespondersOnly((v) => !v);
    setOffset(0);
  }

  const rows = candidates.data ?? [];
  const canPrev = offset > 0;
  const canNext = rows.length === PAGE_SIZE; // full page → maybe more

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <label className="flex items-center gap-2 text-sm text-slate-700">
          <input
            type="checkbox"
            checked={nonRespondersOnly}
            onChange={toggleNonResponders}
          />
          Non-responders only
        </label>
        <div className="flex gap-2">
          <Button variant="secondary" onClick={() => setUploadOpen(true)}>
            Upload file
          </Button>
          <Button onClick={() => setAddOpen(true)}>+ Add candidate</Button>
        </div>
      </div>

      {candidates.isLoading && <LoadingState label="Loading candidates…" />}
      {candidates.isError && (
        <ErrorState error={candidates.error} onRetry={() => candidates.refetch()} />
      )}

      {candidates.data && rows.length === 0 && (
        <EmptyState
          title={
            nonRespondersOnly ? "No non-responders" : "No candidates yet"
          }
          description={
            nonRespondersOnly
              ? "Everyone has responded, or no survey has been sent."
              : "Add candidates individually or upload a CSV/XLSX file."
          }
        />
      )}

      {rows.length > 0 && (
        <Card className="overflow-hidden">
          <table className="w-full text-sm">
            <thead className="border-b border-slate-200 bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-4 py-3 font-medium">Name</th>
                <th className="px-4 py-3 font-medium">Email</th>
                <th className="px-4 py-3 font-medium">Phone</th>
                <th className="px-4 py-3 font-medium">Survey</th>
                <th className="px-4 py-3 font-medium">Call</th>
                <th className="px-4 py-3 font-medium">Opt-outs</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {rows.map((candidate) => (
                <tr key={candidate.id} className="hover:bg-slate-50">
                  <td className="px-4 py-3 font-medium text-slate-900">
                    {candidate.candidate_name ?? "—"}
                  </td>
                  <td className="px-4 py-3 text-slate-600">
                    {candidate.email ?? "—"}
                  </td>
                  <td className="px-4 py-3 text-slate-600">
                    {candidate.phone ?? "—"}
                  </td>
                  <td className="px-4 py-3">
                    <SurveyStatusBadge status={candidate.survey_status} />
                  </td>
                  <td className="px-4 py-3">
                    <CallStatusBadge status={candidate.call_status} />
                  </td>
                  <td className="px-4 py-3 text-xs text-slate-500">
                    {[
                      candidate.opted_out_call ? "call" : null,
                      candidate.opted_out_email ? "email" : null,
                    ]
                      .filter(Boolean)
                      .join(", ") || "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

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

      <AddCandidateModal
        campaignId={campaignId}
        open={addOpen}
        onClose={() => setAddOpen(false)}
        onDone={() => {
          invalidateData();
          setAddOpen(false);
        }}
      />
      <UploadCandidatesModal
        campaignId={campaignId}
        open={uploadOpen}
        onClose={() => setUploadOpen(false)}
        onUploaded={invalidateData}
      />
    </div>
  );
}
