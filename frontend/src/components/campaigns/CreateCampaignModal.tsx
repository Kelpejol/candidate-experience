import { useState } from "react";
import type { FormEvent } from "react";
import { useMutation } from "@tanstack/react-query";

import { createCampaign } from "../../api/campaigns";
import { ASSESSMENT_TOOLS } from "../../lib/types";
import type { AssessmentTool, CampaignCreate, CampaignRead } from "../../lib/types";
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
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    mutation.mutate({
      name: name.trim(),
      tool_name: toolName || undefined,
      response_wait_hours: waitHours,
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

        {mutation.isError && <ErrorState error={mutation.error} />}

        <div className="flex justify-end gap-2 pt-2">
          <Button type="button" variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" disabled={mutation.isPending || !name.trim()}>
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
