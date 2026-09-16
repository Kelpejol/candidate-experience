import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { getCampaign, updateCampaignOutboundSettings } from "../../api/campaigns";
import { CALL_REASONS, CALL_REASON_LABELS } from "../../lib/types";
import type { CallReason } from "../../lib/types";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { FormField, inputClass } from "../ui/FormField";
import { ErrorState, LoadingState } from "../ui/QueryStates";
import { Spinner } from "../ui/Spinner";

/** ISO (naive) -> value for <input type="datetime-local">. */
function toInput(iso: string | null): string {
  return iso ? iso.slice(0, 16) : "";
}

/**
 * Edit the outbound call context — what the calling agent says on the call.
 * These fill the outreach script's [brackets]; the agent receives them as
 * dynamic variables when the call is placed.
 */
export function CampaignOutboundSettingsCard({
  campaignId,
}: {
  campaignId: string;
}) {
  const queryClient = useQueryClient();
  const campaign = useQuery({
    queryKey: ["campaign", campaignId],
    queryFn: () => getCampaign(campaignId),
  });

  if (campaign.isLoading)
    return <LoadingState label="Loading call context…" />;
  if (campaign.isError || !campaign.data)
    return <ErrorState error={campaign.error} onRetry={() => campaign.refetch()} />;

  return <Editor key={campaign.data.id} campaignId={campaignId} initial={campaign.data} onSaved={() => queryClient.invalidateQueries({ queryKey: ["campaign", campaignId] })} />;
}

function Editor({
  campaignId,
  initial,
  onSaved,
}: {
  campaignId: string;
  initial: {
    call_reason: CallReason | null;
    organization_name: string | null;
    assessment_at: string | null;
    assessment_location: string | null;
    practice_test_url: string | null;
    contact_info: string | null;
  };
  onSaved: () => void;
}) {
  const [callReason, setCallReason] = useState<CallReason | "">(
    initial.call_reason ?? "",
  );
  const [orgName, setOrgName] = useState(initial.organization_name ?? "");
  const [assessmentAt, setAssessmentAt] = useState(toInput(initial.assessment_at));
  const [location, setLocation] = useState(initial.assessment_location ?? "");
  const [practiceUrl, setPracticeUrl] = useState(initial.practice_test_url ?? "");
  const [contact, setContact] = useState(initial.contact_info ?? "");

  const urlError =
    practiceUrl.trim() !== "" && !/^https?:\/\/\S+$/.test(practiceUrl.trim())
      ? "Enter a full URL starting with http:// or https://"
      : null;

  const save = useMutation({
    mutationFn: () =>
      updateCampaignOutboundSettings(campaignId, {
        call_reason: callReason || null,
        organization_name: orgName.trim() || null,
        assessment_at: assessmentAt || null,
        assessment_location: location.trim() || null,
        practice_test_url: practiceUrl.trim() || null,
        contact_info: contact.trim() || null,
      }),
    onSuccess: onSaved,
  });

  return (
    <Card className="p-5">
      <p className="text-sm font-semibold text-slate-900">Call context</p>
      <p className="mb-4 mt-0.5 text-xs text-slate-500">
        What the calling agent says on the call — reason, date, and links. The
        agent fills these into its script.
      </p>

      <div className="grid gap-4 sm:grid-cols-2">
        <FormField label="Call reason" htmlFor="oc-reason">
          <select
            id="oc-reason"
            className={inputClass}
            value={callReason}
            onChange={(e) => setCallReason(e.target.value as CallReason | "")}
          >
            <option value="">—</option>
            {CALL_REASONS.map((r) => (
              <option key={r} value={r}>
                {CALL_REASON_LABELS[r]}
              </option>
            ))}
          </select>
        </FormField>

        <FormField label="Calling on behalf of" htmlFor="oc-org">
          <input
            id="oc-org"
            className={inputClass}
            value={orgName}
            onChange={(e) => setOrgName(e.target.value)}
            placeholder="e.g. Dragnet Solutions"
          />
        </FormField>

        <FormField
          label="Assessment date & time"
          htmlFor="oc-at"
          hint="Local time — the agent reads this back exactly as entered. Not converted."
        >
          <input
            id="oc-at"
            type="datetime-local"
            className={inputClass}
            value={assessmentAt}
            onChange={(e) => setAssessmentAt(e.target.value)}
          />
        </FormField>

        <FormField label="Test location or link" htmlFor="oc-loc">
          <input
            id="oc-loc"
            className={inputClass}
            value={location}
            onChange={(e) => setLocation(e.target.value)}
            placeholder="e.g. Lagos test centre"
          />
        </FormField>

        <FormField label="Practice-test link" htmlFor="oc-practice">
          <input
            id="oc-practice"
            className={inputClass}
            value={practiceUrl}
            onChange={(e) => setPracticeUrl(e.target.value)}
            placeholder="https://…"
          />
          {urlError && <p className="mt-1 text-xs text-red-600">{urlError}</p>}
        </FormField>

        <FormField label="Callback contact" htmlFor="oc-contact">
          <input
            id="oc-contact"
            className={inputClass}
            value={contact}
            onChange={(e) => setContact(e.target.value)}
            placeholder="Phone or email the agent gives out"
          />
        </FormField>
      </div>

      <div className="mt-5 flex items-center gap-3 border-t border-slate-100 pt-4">
        <Button
          onClick={() => save.mutate()}
          disabled={save.isPending || Boolean(urlError)}
        >
          {save.isPending ? <Spinner /> : "Save call context"}
        </Button>
        {save.isSuccess && !save.isPending && (
          <span className="text-sm text-emerald-600">Saved.</span>
        )}
      </div>
      {save.isError && (
        <div className="mt-3">
          <ErrorState error={save.error} />
        </div>
      )}
    </Card>
  );
}
