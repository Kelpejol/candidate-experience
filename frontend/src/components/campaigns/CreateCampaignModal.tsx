import { useState } from "react";
import type { FormEvent } from "react";
import { useMutation } from "@tanstack/react-query";

import { createCampaign } from "../../api/campaigns";
import { ASSESSMENT_TOOLS, CALL_REASONS, CALL_REASON_LABELS } from "../../lib/types";
import type {
  AssessmentTool,
  CallReason,
  CampaignCreate,
  CampaignRead,
} from "../../lib/types";
import { Button } from "../ui/Button";
import { FormField, inputClass } from "../ui/FormField";
import { Modal } from "../ui/Modal";
import { ErrorState } from "../ui/QueryStates";
import { Spinner } from "../ui/Spinner";

/**
 * Create-campaign form. Demonstrates the write pattern: a typed useMutation
 * over `createCampaign`, with the tool dropdown driven by the ASSESSMENT_TOOLS
 * const array (so it can't drift from what the API accepts).
 */
export function CreateCampaignModal({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (campaign: CampaignRead) => void;
}) {
  const [name, setName] = useState("");
  const [toolName, setToolName] = useState<AssessmentTool | "">("");
  const [waitHours, setWaitHours] = useState(24);

  // Outbound call context (the outreach script's [brackets]) — all optional.
  const [callReason, setCallReason] = useState<CallReason | "">("");
  const [orgName, setOrgName] = useState("");
  const [assessmentAt, setAssessmentAt] = useState("");
  const [location, setLocation] = useState("");
  const [practiceUrl, setPracticeUrl] = useState("");
  const [contact, setContact] = useState("");

  const urlError =
    practiceUrl.trim() !== "" && !/^https?:\/\/\S+$/.test(practiceUrl.trim())
      ? "Enter a full URL starting with http:// or https://"
      : null;

  const mutation = useMutation({
    mutationFn: (body: CampaignCreate) => createCampaign(body),
    onSuccess: (campaign) => {
      resetForm();
      onCreated(campaign);
    },
  });

  function resetForm() {
    setName("");
    setToolName("");
    setWaitHours(24);
    setCallReason("");
    setOrgName("");
    setAssessmentAt("");
    setLocation("");
    setPracticeUrl("");
    setContact("");
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (urlError) return;
    mutation.mutate({
      name: name.trim(),
      tool_name: toolName || undefined,
      response_wait_hours: waitHours,
      call_reason: callReason || null,
      organization_name: orgName.trim() || null,
      assessment_at: assessmentAt || null,
      assessment_location: location.trim() || null,
      practice_test_url: practiceUrl.trim() || null,
      contact_info: contact.trim() || null,
    });
  }

  return (
    <Modal open={open} onClose={onClose} title="New campaign">
      <form onSubmit={handleSubmit} className="space-y-4">
        <FormField label="Name" htmlFor="campaign-name" required>
          <input
            id="campaign-name"
            className={inputClass}
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Dangote PRP Feedback"
            required
            autoFocus
          />
        </FormField>

        <FormField
          label="Assessment tool"
          htmlFor="campaign-tool"
          hint="Optional. Falls back per-candidate on upload if left blank."
        >
          <select
            id="campaign-tool"
            className={inputClass}
            value={toolName}
            onChange={(e) => setToolName(e.target.value as AssessmentTool | "")}
          >
            <option value="">—</option>
            {ASSESSMENT_TOOLS.map((tool) => (
              <option key={tool} value={tool}>
                {tool}
              </option>
            ))}
          </select>
        </FormField>

        <FormField
          label="Response wait (hours)"
          htmlFor="campaign-wait"
          hint="1–336. How long to wait after the survey before checking non-responders."
        >
          <input
            id="campaign-wait"
            type="number"
            min={1}
            max={336}
            className={inputClass}
            value={waitHours}
            onChange={(e) => setWaitHours(Number(e.target.value))}
          />
        </FormField>

        {/* Outbound call context — what the outreach agent needs to say. */}
        <div className="rounded-lg border border-slate-200 p-4">
          <p className="text-sm font-semibold text-slate-900">
            Outbound call context
          </p>
          <p className="mb-3 mt-0.5 text-xs text-slate-500">
            What the calling agent needs to say. All optional — you can add or
            edit these later on the campaign's Outbound tab.
          </p>

          <div className="space-y-4">
            <FormField label="Call reason" htmlFor="campaign-reason">
              <select
                id="campaign-reason"
                className={inputClass}
                value={callReason}
                onChange={(e) => setCallReason(e.target.value as CallReason | "")}
              >
                <option value="">—</option>
                {CALL_REASONS.map((reason) => (
                  <option key={reason} value={reason}>
                    {CALL_REASON_LABELS[reason]}
                  </option>
                ))}
              </select>
            </FormField>

            <FormField label="Calling on behalf of" htmlFor="campaign-org">
              <input
                id="campaign-org"
                className={inputClass}
                value={orgName}
                onChange={(e) => setOrgName(e.target.value)}
                placeholder="e.g. Dragnet Solutions"
              />
            </FormField>

            <FormField
              label="Assessment date & time"
              htmlFor="campaign-assessment-at"
              hint="Local time — the agent reads this back exactly as entered. Not converted."
            >
              <input
                id="campaign-assessment-at"
                type="datetime-local"
                className={inputClass}
                value={assessmentAt}
                onChange={(e) => setAssessmentAt(e.target.value)}
              />
            </FormField>

            <FormField
              label="Test location or link"
              htmlFor="campaign-location"
            >
              <input
                id="campaign-location"
                className={inputClass}
                value={location}
                onChange={(e) => setLocation(e.target.value)}
                placeholder="e.g. Lagos test centre, or an online test link"
              />
            </FormField>

            <FormField label="Practice-test link" htmlFor="campaign-practice">
              <input
                id="campaign-practice"
                className={inputClass}
                value={practiceUrl}
                onChange={(e) => setPracticeUrl(e.target.value)}
                placeholder="https://…"
              />
              {urlError && (
                <p className="mt-1 text-xs text-red-600">{urlError}</p>
              )}
            </FormField>

            <FormField label="Callback contact" htmlFor="campaign-contact">
              <input
                id="campaign-contact"
                className={inputClass}
                value={contact}
                onChange={(e) => setContact(e.target.value)}
                placeholder="Phone or email the agent gives out"
              />
            </FormField>
          </div>
        </div>

        {mutation.isError && <ErrorState error={mutation.error} />}

        <div className="flex justify-end gap-2 pt-2">
          <Button type="button" variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button
            type="submit"
            disabled={mutation.isPending || !name.trim() || Boolean(urlError)}
          >
            {mutation.isPending ? (
              <>
                <Spinner />
                Creating…
              </>
            ) : (
              "Create campaign"
            )}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
