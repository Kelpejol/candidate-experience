/**
 * Every /campaigns endpoint, as typed functions. Grouped by concern:
 * core → candidates → survey workflow → outbound calls.
 *
 * These are thin, typed wrappers over the shared `api` client — one function
 * per endpoint, named for what it does. UI code calls these; it never builds
 * URLs or touches fetch directly.
 */
import { api, buildQuery } from "../lib/apiClient";
import type {
  CampaignCandidateCreate,
  CampaignCandidateRead,
  CampaignCandidateSurveyStatusUpdate,
  CampaignCreate,
  CampaignRead,
  CampaignStatusUpdate,
  CampaignSummaryRead,
  CampaignSurveyMessageCreate,
  CampaignSurveyMessageCreateResponse,
  CampaignSurveyMessageSend,
  CampaignSurveyMessageSendResponse,
  CampaignSurveyRecipientsPrepare,
  CampaignSurveyRecipientsPrepareResponse,
  CampaignSurveySyncResponse,
  CampaignSurveyTemplateCreate,
  JobQueuedRead,
  OutboundCallAttemptRead,
  OutboundCallAttemptStatusUpdate,
  SurveyMonkeyTemplateRead,
} from "../lib/types";

// --- Core -----------------------------------------------------------------

export function listCampaigns(limit = 50, offset = 0) {
  return api.get<CampaignRead[]>(`/campaigns${buildQuery({ limit, offset })}`);
}

export function createCampaign(body: CampaignCreate) {
  return api.post<CampaignRead>("/campaigns", body);
}

export function getCampaign(campaignId: string) {
  return api.get<CampaignRead>(`/campaigns/${campaignId}`);
}

export function updateCampaignStatus(
  campaignId: string,
  body: CampaignStatusUpdate,
) {
  return api.patch<CampaignRead>(`/campaigns/${campaignId}/status`, body);
}

export function getCampaignSummary(campaignId: string) {
  return api.get<CampaignSummaryRead>(`/campaigns/${campaignId}/summary`);
}

/** SurveyMonkey surveys usable as templates (titles prefixed `TEMPLATE -`). */
export function listSurveyTemplates() {
  return api.get<SurveyMonkeyTemplateRead[]>("/campaigns/survey/templates");
}

// --- Candidates -----------------------------------------------------------

export function listCandidates(campaignId: string, limit = 100, offset = 0) {
  return api.get<CampaignCandidateRead[]>(
    `/campaigns/${campaignId}/candidates${buildQuery({ limit, offset })}`,
  );
}

export function addCandidates(
  campaignId: string,
  candidates: CampaignCandidateCreate[],
) {
  return api.post<CampaignCandidateRead[]>(
    `/campaigns/${campaignId}/candidates`,
    candidates,
  );
}

/** Bulk-add candidates from a .csv/.xlsx file (must have an `email` column). */
export function uploadCandidates(campaignId: string, file: File) {
  const form = new FormData();
  form.append("file", file);
  return api.postForm<CampaignCandidateRead[]>(
    `/campaigns/${campaignId}/candidates/upload`,
    form,
  );
}

export function listNonResponders(campaignId: string, limit = 100, offset = 0) {
  return api.get<CampaignCandidateRead[]>(
    `/campaigns/${campaignId}/non-responders${buildQuery({ limit, offset })}`,
  );
}

export function updateCandidateSurveyStatus(
  campaignId: string,
  candidateId: string,
  body: CampaignCandidateSurveyStatusUpdate,
) {
  return api.patch<CampaignCandidateRead>(
    `/campaigns/${campaignId}/candidates/${candidateId}/survey-status`,
    body,
  );
}

// --- Survey workflow (run these in order) ---------------------------------

export function createSurveyFromTemplate(
  campaignId: string,
  body?: CampaignSurveyTemplateCreate,
) {
  return api.post<CampaignRead>(
    `/campaigns/${campaignId}/survey/create-from-template`,
    body ?? {},
  );
}

export function createSurveyMessage(
  campaignId: string,
  body: CampaignSurveyMessageCreate,
) {
  return api.post<CampaignSurveyMessageCreateResponse>(
    `/campaigns/${campaignId}/survey/message`,
    body,
  );
}

export function prepareSurveyRecipients(
  campaignId: string,
  body: CampaignSurveyRecipientsPrepare,
) {
  return api.post<CampaignSurveyRecipientsPrepareResponse>(
    `/campaigns/${campaignId}/survey/recipients/prepare`,
    body,
  );
}

/** Irreversible — sends real emails. `body.confirm_send` must be exactly
 * SURVEY_SEND_CONFIRMATION. Gate this behind a UI confirmation. */
export function sendSurveyMessage(
  campaignId: string,
  body: CampaignSurveyMessageSend,
) {
  return api.post<CampaignSurveyMessageSendResponse>(
    `/campaigns/${campaignId}/survey/message/send`,
    body,
  );
}

/** Synchronous response sync — blocks until done. Prefer the job variant. */
export function syncSurveyResponses(campaignId: string) {
  return api.post<CampaignSurveySyncResponse>(
    `/campaigns/${campaignId}/survey/sync-responses`,
  );
}

/** Async response sync — returns a job to poll via useJob / getJobStatus. */
export function enqueueSurveySyncJob(campaignId: string) {
  return api.post<JobQueuedRead>(
    `/campaigns/${campaignId}/survey/sync-responses/jobs`,
  );
}

// --- Outbound calls -------------------------------------------------------

export function buildOutboundQueue(campaignId: string) {
  return api.post<OutboundCallAttemptRead[]>(
    `/campaigns/${campaignId}/outbound/build-queue`,
  );
}

export function buildOutboundRetryQueue(campaignId: string, maxAttempts = 3) {
  return api.post<OutboundCallAttemptRead[]>(
    `/campaigns/${campaignId}/outbound/retry-queue${buildQuery({ max_attempts: maxAttempts })}`,
  );
}

export function listOutboundAttempts(
  campaignId: string,
  limit = 100,
  offset = 0,
) {
  return api.get<OutboundCallAttemptRead[]>(
    `/campaigns/${campaignId}/outbound/attempts${buildQuery({ limit, offset })}`,
  );
}

/** The single next queued attempt. Throws ApiError(404) if none is queued. */
export function getNextOutboundAttempt(campaignId: string) {
  return api.get<OutboundCallAttemptRead>(
    `/campaigns/${campaignId}/outbound/attempts/next`,
  );
}

export function updateOutboundAttemptStatus(
  campaignId: string,
  attemptId: string,
  body: OutboundCallAttemptStatusUpdate,
) {
  return api.patch<OutboundCallAttemptRead>(
    `/campaigns/${campaignId}/outbound/attempts/${attemptId}/status`,
    body,
  );
}

/** Async "place the next queued call" — returns a job to poll. */
export function enqueueExecuteNextOutboundJob(campaignId: string) {
  return api.post<JobQueuedRead>(
    `/campaigns/${campaignId}/outbound/execute-next/jobs`,
  );
}
