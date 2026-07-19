import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { listCampaigns } from "../api/campaigns";
import { CreateCampaignModal } from "../components/campaigns/CreateCampaignModal";
import { Badge, CampaignStatusBadge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { EmptyState, ErrorState, LoadingState } from "../components/ui/QueryStates";
import { formatDate } from "../lib/format";

/**
 * Campaigns list + create. The first screen backed by live API data:
 * `listCampaigns` via useQuery, with loading / error / empty states, and a
 * create modal that invalidates this query on success so the new row appears.
 */
export function CampaignsPage() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [createOpen, setCreateOpen] = useState(false);

  const {
    data: campaigns,
    isLoading,
    isError,
    error,
    refetch,
  } = useQuery({
    queryKey: ["campaigns"],
    queryFn: () => listCampaigns(),
  });

  function handleCreated() {
    queryClient.invalidateQueries({ queryKey: ["campaigns"] });
    setCreateOpen(false);
  }

  return (
    <div className="mx-auto max-w-5xl">
      <header className="mb-6 flex items-start justify-between">
        <div>
          <h2 className="text-xl font-semibold text-slate-900">Campaigns</h2>
          <p className="mt-1 text-sm text-slate-600">
            Survey campaigns for the Calling Agent — send a survey, then call
            non-responders.
          </p>
        </div>
        <Button onClick={() => setCreateOpen(true)}>+ New campaign</Button>
      </header>

      {isLoading && <LoadingState label="Loading campaigns…" />}

      {isError && <ErrorState error={error} onRetry={() => refetch()} />}

      {campaigns && campaigns.length === 0 && (
        <EmptyState
          title="No campaigns yet"
          description="Create your first campaign to start sending surveys and placing outbound calls."
          action={
            <Button onClick={() => setCreateOpen(true)}>+ New campaign</Button>
          }
        />
      )}

      {campaigns && campaigns.length > 0 && (
        <Card className="overflow-hidden">
          <table className="w-full text-sm">
            <thead className="border-b border-slate-200 bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-4 py-3 font-medium">Name</th>
                <th className="px-4 py-3 font-medium">Tool</th>
                <th className="px-4 py-3 font-medium">Status</th>
                <th className="px-4 py-3 font-medium">Wait (h)</th>
                <th className="px-4 py-3 font-medium">Survey</th>
                <th className="px-4 py-3 font-medium">Created</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {campaigns.map((campaign) => (
                <tr
                  key={campaign.id}
                  onClick={() => navigate(`/campaigns/${campaign.id}`)}
                  className="cursor-pointer hover:bg-slate-50"
                >
                  <td className="px-4 py-3 font-medium text-slate-900">
                    {campaign.name}
                  </td>
                  <td className="px-4 py-3 text-slate-600">
                    {campaign.tool_name ?? "—"}
                  </td>
                  <td className="px-4 py-3">
                    <CampaignStatusBadge status={campaign.status} />
                  </td>
                  <td className="px-4 py-3 text-slate-600">
                    {campaign.response_wait_hours}
                  </td>
                  <td className="px-4 py-3">
                    {campaign.survey_id ? (
                      <Badge tone="green">Linked</Badge>
                    ) : (
                      <Badge tone="gray">Not set</Badge>
                    )}
                  </td>
                  <td className="px-4 py-3 text-slate-500">
                    {formatDate(campaign.created_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      <CreateCampaignModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={handleCreated}
      />
    </div>
  );
}
