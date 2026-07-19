import { useState } from "react";
import type { FormEvent } from "react";
import { useMutation } from "@tanstack/react-query";

import { uploadCandidates } from "../../api/campaigns";
import { Button } from "../ui/Button";
import { FormField, inputClass } from "../ui/FormField";
import { Modal } from "../ui/Modal";
import { ErrorState } from "../ui/QueryStates";
import { Spinner } from "../ui/Spinner";

/**
 * Bulk candidate upload (.csv/.xlsx). Stays open on success to show how many
 * were added — the file must have an `email` column; phone numbers are
 * normalized to Nigerian +234 format server-side.
 */
export function UploadCandidatesModal({
  campaignId,
  open,
  onClose,
  onUploaded,
}: {
  campaignId: string;
  open: boolean;
  onClose: () => void;
  onUploaded: () => void;
}) {
  const [file, setFile] = useState<File | null>(null);

  const mutation = useMutation({
    mutationFn: (selected: File) => uploadCandidates(campaignId, selected),
    onSuccess: () => onUploaded(),
  });

  function handleClose() {
    setFile(null);
    mutation.reset();
    onClose();
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (file) mutation.mutate(file);
  }

  return (
    <Modal open={open} onClose={handleClose} title="Upload candidates">
      <form onSubmit={handleSubmit} className="space-y-4">
        <FormField
          label="CSV or XLSX file"
          htmlFor="cand-file"
          hint="Must include an 'email' column. Optional: candidate_name, phone, tool_name, campaign_name, external_candidate_id."
        >
          <input
            id="cand-file"
            type="file"
            accept=".csv,.xlsx"
            className={inputClass}
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
        </FormField>

        {mutation.isError && <ErrorState error={mutation.error} />}

        {mutation.isSuccess && (
          <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-700">
            ✓ Added {mutation.data.length} candidate
            {mutation.data.length === 1 ? "" : "s"}.
          </div>
        )}

        <div className="flex justify-end gap-2 pt-2">
          <Button type="button" variant="secondary" onClick={handleClose}>
            {mutation.isSuccess ? "Done" : "Cancel"}
          </Button>
          <Button type="submit" disabled={!file || mutation.isPending}>
            {mutation.isPending ? (
              <>
                <Spinner />
                Uploading…
              </>
            ) : (
              "Upload"
            )}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
