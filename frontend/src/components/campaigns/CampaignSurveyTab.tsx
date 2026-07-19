import { useEffect, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createSurveyFromTemplate,
  createSurveyMessage,
  enqueueSurveySyncJob,
  listSurveyTemplates,
  prepareSurveyRecipients,
  sendSurveyMessage,
} from "../../api/campaigns";
import { useJob } from "../../hooks/useJob";
import { humanize } from "../../lib/format";
import { SURVEY_SEND_CONFIRMATION } from "../../lib/types";
import type {
  CampaignRead,
  CampaignSurveyMessageCreateResponse,
  CampaignSurveyRecipientsPrepareResponse,
} from "../../lib/types";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { FormField, inputClass } from "../ui/FormField";
import { Modal } from "../ui/Modal";
import { ErrorState } from "../ui/QueryStates";
import { Spinner } from "../ui/Spinner";

/** One numbered step in the guided survey flow. */
function StepCard({
  index,
  title,
  description,
  enabled,
  done,
  children,
}: {
  index: number;
  title: string;
  description: string;
  enabled: boolean;
  done: boolean;
  children: ReactNode;
}) {
  return (
    <Card className={`p-5 ${!enabled && !done ? "opacity-55" : ""}`}>
      <div className="flex items-center gap-3">
        <span
          className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-sm font-semibold ${
            done
              ? "bg-emerald-500 text-white"
              : enabled
                ? "bg-slate-900 text-white"
                : "bg-slate-200 text-slate-500"
          }`}
        >
          {done ? "✓" : index}
        </span>
        <div>
          <h3 className="text-sm font-semibold text-slate-900">{title}</h3>
          <p className="text-xs text-slate-500">{description}</p>
        </div>
      </div>
      <div className="mt-4 pl-10">{children}</div>
    </Card>
  );
}

export function CampaignSurveyTab({ campaign }: { campaign: CampaignRead }) {
  const queryClient = useQueryClient();

  // Step outputs that aren't fully re-derivable from the campaign object.
  const [message, setMessage] =
    useState<CampaignSurveyMessageCreateResponse | null>(null);
  const [prepared, setPrepared] =
    useState<CampaignSurveyRecipientsPrepareResponse | null>(null);
  const [sent, setSent] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);

  const surveyId = campaign.survey_id;

  function invalidateCampaign() {
    queryClient.invalidateQueries({ queryKey: ["campaign", campaign.id] });
  }

  // --- Step 1: create survey from template --------------------------------
  const templates = useQuery({
    queryKey: ["survey-templates"],
    queryFn: listSurveyTemplates,
  });
  const [templateId, setTemplateId] = useState("");
  const createSurvey = useMutation({
    mutationFn: () =>
      createSurveyFromTemplate(campaign.id, {
        template_survey_id: templateId || undefined,
      }),
    onSuccess: invalidateCampaign,
  });

  // --- Step 2: create collector message -----------------------------------
  const [subject, setSubject] = useState("Share your assessment experience");
  const [collectorName, setCollectorName] = useState("");
  const createMessage = useMutation({
    mutationFn: () =>
      createSurveyMessage(campaign.id, {
        subject: subject.trim(),
        collector_name: collectorName.trim() || undefined,
      }),
    onSuccess: (res) => {
      setMessage(res);
      setPrepared(null);
      setSent(false);
      invalidateCampaign();
    },
  });

  // --- Step 3: prepare recipients -----------------------------------------
  const prepare = useMutation({
    mutationFn: () =>
      prepareSurveyRecipients(campaign.id, {
        message_id: message!.message_id,
        collector_id: message!.collector_id,
      }),
    onSuccess: (res) => setPrepared(res),
  });

  // --- Step 4: send (irreversible) ----------------------------------------
  const send = useMutation({
    mutationFn: () =>
      sendSurveyMessage(campaign.id, {
        message_id: message!.message_id,
        collector_id: message!.collector_id,
        confirm_send: SURVEY_SEND_CONFIRMATION,
      }),
    onSuccess: () => {
      setSent(true);
      setConfirmOpen(false);
      invalidateCampaign();
      queryClient.invalidateQueries({
        queryKey: ["campaign-summary", campaign.id],
      });
    },
  });

  return (
    <div className="space-y-4">
      <StepCard
        index={1}
        title="Create the campaign survey"
        description="Clone the SurveyMonkey template into a survey for this campaign."
        enabled
        done={Boolean(surveyId)}
      >
        {surveyId ? (
          <p className="text-sm text-slate-600">
            Survey created: <code className="text-xs">{surveyId}</code>
          </p>
        ) : (
          <div className="flex items-end gap-3">
            <FormField label="Template" htmlFor="tmpl" hint="Blank = default template">
              <select
                id="tmpl"
                className={inputClass}
                value={templateId}
                onChange={(e) => setTemplateId(e.target.value)}
              >
                <option value="">Default template</option>
                {templates.data?.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.title}
                  </option>
                ))}
              </select>
            </FormField>
            <Button
              onClick={() => createSurvey.mutate()}
              disabled={createSurvey.isPending}
            >
              {createSurvey.isPending ? <Spinner /> : "Create survey"}
            </Button>
          </div>
        )}
        {createSurvey.isError && (
          <div className="mt-3">
            <ErrorState error={createSurvey.error} />
          </div>
        )}
      </StepCard>

      <StepCard
        index={2}
        title="Create the email message"
        description="Set the subject line. SurveyMonkey owns the email body/design."
        enabled={Boolean(surveyId)}
        done={Boolean(message)}
      >
        <form
          className="space-y-3"
          onSubmit={(e: FormEvent) => {
            e.preventDefault();
            createMessage.mutate();
          }}
        >
          <FormField label="Subject" htmlFor="subject" required>
            <input
              id="subject"
              className={inputClass}
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              disabled={!surveyId}
              required
            />
          </FormField>
          <FormField label="Collector name" htmlFor="collector" hint="Optional">
            <input
              id="collector"
              className={inputClass}
              value={collectorName}
              onChange={(e) => setCollectorName(e.target.value)}
              disabled={!surveyId}
            />
          </FormField>
          {message && (
            <p className="text-sm text-emerald-700">
              ✓ Message created (collector {message.collector_id})
            </p>
          )}
          {createMessage.isError && <ErrorState error={createMessage.error} />}
          <Button
            type="submit"
            disabled={!surveyId || createMessage.isPending || !subject.trim()}
          >
            {createMessage.isPending ? <Spinner /> : "Create message"}
          </Button>
        </form>
      </StepCard>

      <StepCard
        index={3}
        title="Prepare recipients"
        description="Add eligible candidates to the message. No emails sent yet."
        enabled={Boolean(message)}
        done={Boolean(prepared)}
      >
        {prepared && (
          <p className="mb-2 text-sm text-slate-600">
            Prepared {prepared.prepared_recipients} of{" "}
            {prepared.eligible_candidates} eligible ({prepared.skipped_candidates}{" "}
            skipped).
          </p>
        )}
        {prepare.isError && (
          <div className="mb-2">
            <ErrorState error={prepare.error} />
          </div>
        )}
        <Button
          onClick={() => prepare.mutate()}
          disabled={!message || prepare.isPending}
        >
          {prepare.isPending ? <Spinner /> : "Prepare recipients"}
        </Button>
      </StepCard>

      <StepCard
        index={4}
        title="Send survey emails"
        description="Irreversible — sends real emails to prepared recipients."
        enabled={Boolean(prepared)}
        done={sent}
      >
        {sent ? (
          <p className="text-sm text-emerald-700">✓ Survey emails sent.</p>
        ) : (
          <Button
            variant="danger"
            onClick={() => setConfirmOpen(true)}
            disabled={!prepared}
          >
            Send survey emails
          </Button>
        )}
      </StepCard>

      {/* Response sync — async job with live polling */}
      <SyncPanel campaignId={campaign.id} />

      {/* Irreversible-send confirmation */}
      <Modal
        open={confirmOpen}
        onClose={() => setConfirmOpen(false)}
        title="Send survey emails?"
      >
        <p className="text-sm text-slate-600">
          This sends real survey invitation emails to all prepared recipients
          for <span className="font-medium">{campaign.name}</span>. This cannot
          be undone.
        </p>
        {send.isError && (
          <div className="mt-3">
            <ErrorState error={send.error} />
          </div>
        )}
        <div className="mt-5 flex justify-end gap-2">
          <Button variant="secondary" onClick={() => setConfirmOpen(false)}>
            Cancel
          </Button>
          <Button
            variant="danger"
            onClick={() => send.mutate()}
            disabled={send.isPending}
          >
            {send.isPending ? (
              <>
                <Spinner />
                Sending…
              </>
            ) : (
              "Yes, send emails"
            )}
          </Button>
        </div>
      </Modal>
    </div>
  );
}

/**
 * Async response sync. Enqueues the job, then polls it live with `useJob`,
 * showing status transitions and the final result — the reference example of
 * the 202-then-poll pattern.
 */
function SyncPanel({ campaignId }: { campaignId: string }) {
  const queryClient = useQueryClient();
  const [jobId, setJobId] = useState<string | null>(null);

  const enqueue = useMutation({
    mutationFn: () => enqueueSurveySyncJob(campaignId),
    onSuccess: (res) => setJobId(res.job_id),
  });

  const job = useJob(jobId);

  useEffect(() => {
    if (job.isFinished) {
      queryClient.invalidateQueries({ queryKey: ["candidates", campaignId] });
      queryClient.invalidateQueries({
        queryKey: ["campaign-summary", campaignId],
      });
    }
  }, [job.isFinished, campaignId, queryClient]);

  return (
    <Card className="p-5">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-slate-900">
            Sync survey responses
          </h3>
          <p className="text-xs text-slate-500">
            Pull the latest responses from SurveyMonkey and update candidate
            statuses (runs as a background job).
          </p>
        </div>
        <Button
          onClick={() => enqueue.mutate()}
          disabled={enqueue.isPending || job.isPolling}
        >
          {enqueue.isPending || job.isPolling ? <Spinner /> : "Sync now"}
        </Button>
      </div>

      {enqueue.isError && (
        <div className="mt-3">
          <ErrorState error={enqueue.error} />
        </div>
      )}

      {jobId && (
        <div className="mt-4 rounded-lg border border-slate-200 bg-slate-50 p-3 text-sm">
          <p className="text-slate-600">
            Job <code className="text-xs">{jobId}</code> ·{" "}
            <span className="font-medium">
              {job.status ? humanize(job.status) : "Starting…"}
            </span>
            {job.isPolling && <Spinner className="ml-2 text-slate-400" />}
          </p>

          {job.isFailed && (
            <p className="mt-2 text-red-600">{job.data?.error}</p>
          )}

          {job.isFinished && job.data?.result && (
            <ul className="mt-2 space-y-1">
              {Object.entries(job.data.result).map(([key, value]) => (
                <li key={key} className="flex justify-between">
                  <span className="text-slate-600">{humanize(key)}</span>
                  <span className="font-medium text-slate-900">
                    {String(value)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </Card>
  );
}
