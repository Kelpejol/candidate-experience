import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { reindexCampaignKb, updateCampaignInbound } from "../../api/campaigns";
import type { CampaignRead } from "../../lib/types";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { FormField, inputClass } from "../ui/FormField";
import { ErrorState } from "../ui/QueryStates";
import { Spinner } from "../ui/Spinner";

const SCOPE_RE = /^[a-z0-9-]+$/;

/** ISO (naive UTC) -> value for <input type="datetime-local"> (no tz shift). */
function toInput(iso: string | null): string {
  return iso ? iso.slice(0, 16) : "";
}
/** datetime-local value -> naive ISO for the API, or null when empty. */
function fromInput(value: string): string | null {
  return value ? value : null;
}

export function CampaignInboundTab({ campaign }: { campaign: CampaignRead }) {
  const queryClient = useQueryClient();

  const [inboundActive, setInboundActive] = useState(campaign.inbound_active);
  const [activeFrom, setActiveFrom] = useState(toInput(campaign.active_from));
  const [activeUntil, setActiveUntil] = useState(toInput(campaign.active_until));
  const [kbSource, setKbSource] = useState(campaign.kb_source ?? "");
  const [kbScope, setKbScope] = useState(campaign.kb_scope ?? "");

  // --- client-side validation ---------------------------------------------
  const scopeError =
    kbScope.trim() !== "" && !SCOPE_RE.test(kbScope.trim())
      ? "Lowercase letters, numbers and hyphens only — e.g. dangote-grad."
      : null;
  const windowError =
    activeFrom && activeUntil && new Date(activeFrom) >= new Date(activeUntil)
      ? "The end time must be after the start time."
      : null;
  const hasErrors = Boolean(scopeError || windowError);

  const savedSource = campaign.kb_source ?? "";
  const sourceUnsaved = kbSource.trim() !== savedSource.trim();

  const save = useMutation({
    mutationFn: () =>
      updateCampaignInbound(campaign.id, {
        inbound_active: inboundActive,
        active_from: fromInput(activeFrom),
        active_until: fromInput(activeUntil),
        kb_source: kbSource.trim() || null,
        kb_scope: kbScope.trim() || null,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["campaign", campaign.id] });
    },
  });

  const reindex = useMutation({
    mutationFn: () => reindexCampaignKb(campaign.id),
  });

  const reindexDisabled = !savedSource || sourceUnsaved || reindex.isPending;

  return (
    <div className="space-y-6">
      {/* Inbound availability */}
      <Card className="p-5">
        <p className="text-sm font-semibold text-slate-900">Inbound availability</p>
        <p className="mb-4 mt-0.5 text-xs text-slate-500">
          Controls whether the phone agent will scope to and answer for this campaign.
        </p>

        <label className="flex items-center gap-3">
          <input
            type="checkbox"
            checked={inboundActive}
            onChange={(e) => setInboundActive(e.target.checked)}
            className="h-4 w-4 rounded border-slate-300"
          />
          <span className="text-sm text-slate-800">
            Available to inbound callers
          </span>
        </label>

        <div className="mt-5 grid gap-4 sm:grid-cols-2">
          <FormField label="Active from (UTC, optional)" htmlFor="active-from">
            <input
              id="active-from"
              type="datetime-local"
              className={inputClass}
              value={activeFrom}
              onChange={(e) => setActiveFrom(e.target.value)}
            />
          </FormField>
          <FormField label="Active until (UTC, optional)" htmlFor="active-until">
            <input
              id="active-until"
              type="datetime-local"
              className={inputClass}
              value={activeUntil}
              onChange={(e) => setActiveUntil(e.target.value)}
            />
          </FormField>
        </div>
        {windowError && <p className="mt-2 text-xs text-red-600">{windowError}</p>}
        <p className="mt-2 text-xs text-slate-400">
          Leave the window empty for “always on while available”. Times are UTC.
        </p>
      </Card>

      {/* Knowledge base */}
      <Card className="p-5">
        <p className="text-sm font-semibold text-slate-900">Knowledge base</p>
        <p className="mb-4 mt-0.5 text-xs text-slate-500">
          Where this campaign's answers come from, and the tag its content is filed under.
        </p>

        <div className="space-y-4">
          <FormField label="KB source" htmlFor="kb-source">
            <input
              id="kb-source"
              type="text"
              className={inputClass}
              placeholder="Folder path (e.g. kb_voice) — SharePoint link later"
              value={kbSource}
              onChange={(e) => setKbSource(e.target.value)}
            />
          </FormField>
          <FormField label="KB scope tag" htmlFor="kb-scope">
            <input
              id="kb-scope"
              type="text"
              className={inputClass}
              placeholder="e.g. dangote-grad"
              value={kbScope}
              onChange={(e) => setKbScope(e.target.value)}
            />
          </FormField>
          {scopeError && <p className="text-xs text-red-600">{scopeError}</p>}
          <p className="text-xs text-slate-400">
            The scope tag filters retrieval to this campaign (falls back to the
            campaign id if left blank).
          </p>
        </div>

        <div className="mt-5 flex flex-wrap items-center gap-3 border-t border-slate-100 pt-4">
          <Button onClick={() => reindex.mutate()} disabled={reindexDisabled}>
            {reindex.isPending ? <Spinner /> : "Re-index KB"}
          </Button>
          {!savedSource && (
            <span className="text-xs text-slate-400">Set and save a KB source first.</span>
          )}
          {savedSource && sourceUnsaved && (
            <span className="text-xs text-amber-600">Save your KB source change first.</span>
          )}
          {reindex.isSuccess && (
            <span className="text-xs text-emerald-600">
              Indexed {reindex.data.reindexed} chunks into “{reindex.data.scope}”.
            </span>
          )}
        </div>
        {reindex.isError && (
          <div className="mt-3">
            <ErrorState error={reindex.error} />
          </div>
        )}
      </Card>

      {/* Save bar */}
      <div className="flex items-center gap-3">
        <Button onClick={() => save.mutate()} disabled={hasErrors || save.isPending}>
          {save.isPending ? <Spinner /> : "Save inbound settings"}
        </Button>
        {save.isSuccess && !save.isPending && (
          <span className="text-sm text-emerald-600">Saved.</span>
        )}
      </div>
      {save.isError && <ErrorState error={save.error} />}
    </div>
  );
}
