import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { getCampaignSummary, updateCampaignStatus } from "../../api/campaigns";
import { formatDateTime, humanize } from "../../lib/format";
import { CAMPAIGN_STATUSES } from "../../lib/types";
import type { CampaignRead, CampaignStatus } from "../../lib/types";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { FormField, inputClass } from "../ui/FormField";
import { ErrorState, LoadingState } from "../ui/QueryStates";
import { Spinner } from "../ui/Spinner";
import { StatTile } from "../ui/StatTile";

/** A key→count breakdown card (survey statuses, call statuses, etc.). */
function Breakdown({ title, data }: { title: string; data: Record<string, number> }) {
  const entries = Object.entries(data);
  return (
    <Card className="p-4">
      <p className="text-xs uppercase tracking-wide text-slate-500">{title}</p>
      {entries.length === 0 ? (
        <p className="mt-2 text-sm text-slate-400">No data yet</p>
      ) : (
        <ul className="mt-2 space-y-1">
          {entries.map(([key, count]) => (
            <li key={key} className="flex justify-between text-sm">
              <span className="text-slate-600">{humanize(key)}</span>
              <span className="font-medium text-slate-900">{count}</span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

/** A label→value row in the campaign meta grid. */
function Meta({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-slate-400">{label}</dt>
      <dd className="mt-0.5 text-sm text-slate-800">{value}</dd>
    </div>
  );
}

export function CampaignSummaryTab({ campaign }: { campaign: CampaignRead }) {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<CampaignStatus>(campaign.status);

  // Keep the select in sync if the campaign is refetched with a new status.
  useEffect(() => setStatus(campaign.status), [campaign.status]);

  const summary = useQuery({
    queryKey: ["campaign-summary", campaign.id],
    queryFn: () => getCampaignSummary(campaign.id),
  });

  const statusMutation = useMutation({
    mutationFn: (next: CampaignStatus) =>
      updateCampaignStatus(campaign.id, { status: next }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["campaign", campaign.id] });
      queryClient.invalidateQueries({ queryKey: ["campaigns"] });
    },
  });

  return (
    <div className="space-y-6">
      {/* Meta + status control */}
      <Card className="p-5">
        <dl className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-3">
          <Meta label="Tool" value={campaign.tool_name ?? "—"} />
          <Meta label="Response wait" value={`${campaign.response_wait_hours} h`} />
          <Meta
            label="SurveyMonkey survey"
            value={campaign.survey_id ?? "Not created"}
          />
          <Meta
            label="Collector"
            value={campaign.surveymonkey_collector_id ?? "—"}
          />
          <Meta label="Created" value={formatDateTime(campaign.created_at)} />
          <Meta
            label="Survey sent"
            value={formatDateTime(campaign.survey_sent_at)}
          />
        </dl>

        <div className="mt-5 flex items-end gap-3 border-t border-slate-100 pt-4">
          <FormField label="Lifecycle status" htmlFor="status-select">
            <select
              id="status-select"
              className={inputClass}
              value={status}
              onChange={(e) => setStatus(e.target.value as CampaignStatus)}
            >
              {CAMPAIGN_STATUSES.map((s) => (
                <option key={s} value={s}>
                  {humanize(s)}
                </option>
              ))}
            </select>
          </FormField>
          <Button
            onClick={() => statusMutation.mutate(status)}
            disabled={statusMutation.isPending || status === campaign.status}
          >
            {statusMutation.isPending ? <Spinner /> : "Update status"}
          </Button>
        </div>
        {statusMutation.isError && (
          <div className="mt-3">
            <ErrorState error={statusMutation.error} />
          </div>
        )}
      </Card>

      {/* Aggregate summary */}
      {summary.isLoading && <LoadingState label="Loading summary…" />}
      {summary.isError && (
        <ErrorState error={summary.error} onRetry={() => summary.refetch()} />
      )}
      {summary.data && (
        <>
          <StatTile
            label="Total candidates"
            value={summary.data.total_candidates}
          />
          <div className="grid gap-4 sm:grid-cols-3">
            <Breakdown title="Survey statuses" data={summary.data.survey_statuses} />
            <Breakdown title="Call statuses" data={summary.data.call_statuses} />
            <Breakdown
              title="Outbound attempts"
              data={summary.data.outbound_attempts}
            />
          </div>
        </>
      )}
    </div>
  );
}
