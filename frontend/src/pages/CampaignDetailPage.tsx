import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import { getCampaign } from "../api/campaigns";
import { CampaignCandidatesTab } from "../components/campaigns/CampaignCandidatesTab";
import { CampaignOutboundTab } from "../components/campaigns/CampaignOutboundTab";
import { CampaignSummaryTab } from "../components/campaigns/CampaignSummaryTab";
import { CampaignSurveyTab } from "../components/campaigns/CampaignSurveyTab";
import { CampaignStatusBadge } from "../components/ui/Badge";
import { ErrorState, LoadingState } from "../components/ui/QueryStates";

type TabKey = "summary" | "candidates" | "survey" | "outbound";

const TABS: { key: TabKey; label: string }[] = [
  { key: "summary", label: "Summary" },
  { key: "candidates", label: "Candidates" },
  { key: "survey", label: "Survey" },
  { key: "outbound", label: "Outbound" },
];

export function CampaignDetailPage() {
  const { campaignId } = useParams<{ campaignId: string }>();
  const [tab, setTab] = useState<TabKey>("summary");

  const {
    data: campaign,
    isLoading,
    isError,
    error,
    refetch,
  } = useQuery({
    queryKey: ["campaign", campaignId],
    queryFn: () => getCampaign(campaignId as string),
    enabled: Boolean(campaignId),
  });

  return (
    <div className="mx-auto max-w-5xl">
      <Link
        to="/campaigns"
        className="text-sm text-slate-500 hover:text-slate-700"
      >
        ← Campaigns
      </Link>

      {isLoading && <LoadingState label="Loading campaign…" />}
      {isError && (
        <div className="mt-4">
          <ErrorState error={error} onRetry={() => refetch()} />
        </div>
      )}

      {campaign && (
        <>
          <header className="mt-2 mb-5 flex items-center gap-3">
            <h2 className="text-xl font-semibold text-slate-900">
              {campaign.name}
            </h2>
            <CampaignStatusBadge status={campaign.status} />
          </header>

          <div className="mb-6 flex gap-1 border-b border-slate-200">
            {TABS.map((t) => (
              <button
                key={t.key}
                onClick={() => setTab(t.key)}
                className={`-mb-px border-b-2 px-4 py-2 text-sm font-medium transition-colors ${
                  tab === t.key
                    ? "border-slate-900 text-slate-900"
                    : "border-transparent text-slate-500 hover:text-slate-700"
                }`}
              >
                {t.label}
              </button>
            ))}
          </div>

          {tab === "summary" && <CampaignSummaryTab campaign={campaign} />}
          {tab === "candidates" && (
            <CampaignCandidatesTab campaignId={campaign.id} />
          )}
          {tab === "survey" && <CampaignSurveyTab campaign={campaign} />}
          {tab === "outbound" && (
            <CampaignOutboundTab campaignId={campaign.id} />
          )}
        </>
      )}
    </div>
  );
}
