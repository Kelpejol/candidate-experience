import { useState } from "react";
import type { FormEvent } from "react";
import { useMutation } from "@tanstack/react-query";

import { addCandidates } from "../../api/campaigns";
import type { CampaignCandidateCreate } from "../../lib/types";
import { Button } from "../ui/Button";
import { FormField, inputClass } from "../ui/FormField";
import { Modal } from "../ui/Modal";
import { ErrorState } from "../ui/QueryStates";
import { Spinner } from "../ui/Spinner";

/** Add a single candidate (the API takes an array; we send one). */
export function AddCandidateModal({
  campaignId,
  open,
  onClose,
  onDone,
}: {
  campaignId: string;
  open: boolean;
  onClose: () => void;
  onDone: () => void;
}) {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [externalId, setExternalId] = useState("");
  const [optOutCall, setOptOutCall] = useState(false);
  const [optOutEmail, setOptOutEmail] = useState(false);

  const mutation = useMutation({
    mutationFn: (body: CampaignCandidateCreate) =>
      addCandidates(campaignId, [body]),
    onSuccess: () => {
      reset();
      onDone();
    },
  });

  function reset() {
    setName("");
    setEmail("");
    setPhone("");
    setExternalId("");
    setOptOutCall(false);
    setOptOutEmail(false);
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    mutation.mutate({
      candidate_name: name.trim() || undefined,
      email: email.trim() || undefined,
      phone: phone.trim() || undefined,
      external_candidate_id: externalId.trim() || undefined,
      opted_out_call: optOutCall,
      opted_out_email: optOutEmail,
    });
  }

  return (
    <Modal open={open} onClose={onClose} title="Add candidate">
      <form onSubmit={handleSubmit} className="space-y-4">
        <FormField label="Name" htmlFor="cand-name">
          <input
            id="cand-name"
            className={inputClass}
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </FormField>
        <FormField label="Email" htmlFor="cand-email">
          <input
            id="cand-email"
            type="email"
            className={inputClass}
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="candidate@example.com"
          />
        </FormField>
        <FormField
          label="Phone"
          htmlFor="cand-phone"
          hint="Nigerian numbers are normalized to +234 on upload; here, enter as-is."
        >
          <input
            id="cand-phone"
            className={inputClass}
            value={phone}
            onChange={(e) => setPhone(e.target.value)}
            placeholder="+2348012345678"
          />
        </FormField>
        <FormField label="External candidate ID" htmlFor="cand-ext">
          <input
            id="cand-ext"
            className={inputClass}
            value={externalId}
            onChange={(e) => setExternalId(e.target.value)}
            placeholder="ATS-991"
          />
        </FormField>

        <div className="flex gap-6">
          <label className="flex items-center gap-2 text-sm text-slate-700">
            <input
              type="checkbox"
              checked={optOutCall}
              onChange={(e) => setOptOutCall(e.target.checked)}
            />
            Opted out of calls
          </label>
          <label className="flex items-center gap-2 text-sm text-slate-700">
            <input
              type="checkbox"
              checked={optOutEmail}
              onChange={(e) => setOptOutEmail(e.target.checked)}
            />
            Opted out of email
          </label>
        </div>

        {mutation.isError && <ErrorState error={mutation.error} />}

        <div className="flex justify-end gap-2 pt-2">
          <Button type="button" variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" disabled={mutation.isPending}>
            {mutation.isPending ? <Spinner /> : "Add candidate"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
