/**
 * TypeScript mirror of the backend's request/response schemas.
 *
 * Source of truth: docs/api-reference.md and the Pydantic schemas in
 * app/schemas/*.py. Keep this file in sync with those.
 *
 * Conventions:
 *  - Datetimes arrive as ISO 8601 strings over JSON → typed as `string`.
 *  - `Read` types (responses) use `| null` for optional fields, because the
 *    backend serializes absent optionals as JSON null.
 *  - `Create`/`Update` types (request bodies) use `?` for optional fields,
 *    because the client may simply omit them.
 *  - Enum-like literals are declared as `const` arrays first, then a derived
 *    union type — so the UI gets BOTH a compile-time type AND an iterable
 *    list to build dropdowns/filters from.
 */

// ---------------------------------------------------------------------------
// Controlled vocabularies (app/core/vocabulary.py + app/schemas/*)
// ---------------------------------------------------------------------------

export const ASSESSMENT_TOOLS = ["FOT", "Scholastica"] as const;
export type AssessmentTool = (typeof ASSESSMENT_TOOLS)[number];

export const CALL_DIRECTIONS = ["inbound", "outbound"] as const;
export type CallDirection = (typeof CALL_DIRECTIONS)[number];

export const CALL_DISPOSITIONS = [
  "answered_by_ai",
  "handed_off_to_human",
  "handoff_failed",
  "missed",
  "voicemail",
  "no_answer",
  "opted_out",
  "failed",
] as const;
export type CallDisposition = (typeof CALL_DISPOSITIONS)[number];

export const CAMPAIGN_STATUSES = [
  "draft",
  "uploaded",
  "survey_sending",
  "survey_sent",
  "waiting_for_responses",
  "non_response_checking",
  "outbound_ready",
  "outbound_calling",
  "completed",
  "failed",
] as const;
export type CampaignStatus = (typeof CAMPAIGN_STATUSES)[number];

export const SURVEY_STATUSES = [
  "not_sent",
  "sent",
  "responded",
  "partial_response",
  "non_responder",
  "excluded_opt_out",
  "failed",
] as const;
export type SurveyStatus = (typeof SURVEY_STATUSES)[number];

export const CALL_STATUSES = [
  "not_queued",
  "queued",
  "calling",
  "answered",
  "responded_by_call",
  "no_answer",
  "busy",
  "voicemail",
  "failed",
  "opted_out",
  "handed_off_to_human",
] as const;
export type CallStatus = (typeof CALL_STATUSES)[number];

export const OUTBOUND_ATTEMPT_STATUSES = [
  "queued",
  "calling",
  "answered",
  "responded_by_call",
  "no_answer",
  "busy",
  "voicemail",
  "failed",
  "opted_out",
  "handed_off_to_human",
] as const;
export type OutboundCallAttemptStatus =
  (typeof OUTBOUND_ATTEMPT_STATUSES)[number];

// ---------------------------------------------------------------------------
// Call records (app/schemas/call_record.py)
// ---------------------------------------------------------------------------

export interface CallRecordRead {
  id: string;
  external_call_id: string;
  direction: string;
  candidate_phone: string | null;
  candidate_name: string | null;
  tool_name: string | null;
  campaign_name: string | null;
  disposition: string;
  issue_summary: string | null;
  transcription: string | null;
  recording_url: string | null;
  call_start_time: string | null;
  call_end_time: string | null;
  created_at: string;
  handoff_call_sid: string | null;
  handoff_status: string | null;
  handoff_duration: number | null;
  handoff_bridged: boolean | null;
}

export interface CallRecordCreate {
  external_call_id: string;
  direction: CallDirection;
  disposition: CallDisposition;
  candidate_phone?: string | null;
  candidate_name?: string | null;
  tool_name?: AssessmentTool | null;
  campaign_name?: string | null;
  issue_summary?: string | null;
  transcription?: string | null;
  recording_url?: string | null;
  handoff_call_sid?: string | null;
  handoff_status?: string | null;
  handoff_duration?: number | null;
  handoff_bridged?: boolean | null;
  call_start_time?: string | null;
  call_end_time?: string | null;
}

export interface CallRecordResponse {
  accepted: boolean;
  external_call_id: string;
  message: string;
}

// ---------------------------------------------------------------------------
// Campaigns (app/schemas/campaign.py)
// ---------------------------------------------------------------------------

export interface CampaignRead {
  id: string;
  name: string;
  tool_name: string | null;
  survey_id: string | null;
  surveymonkey_collector_id: string | null;
  status: CampaignStatus;
  response_wait_hours: number;
  created_at: string;
  survey_sent_at: string | null;
  non_responder_checked_at: string | null;
}

export interface CampaignCreate {
  name: string;
  tool_name?: AssessmentTool | null;
  survey_id?: string | null;
  surveymonkey_collector_id?: string | null;
  response_wait_hours?: number; // default 24, range 1–336
}

export interface CampaignStatusUpdate {
  status: CampaignStatus;
}

export interface CampaignSummaryRead {
  campaign_id: string;
  total_candidates: number;
  survey_statuses: Record<string, number>;
  call_statuses: Record<string, number>;
  outbound_attempts: Record<string, number>;
}

// ---------------------------------------------------------------------------
// Campaign candidates
// ---------------------------------------------------------------------------

export interface CampaignCandidateRead {
  id: string;
  campaign_id: string;
  candidate_name: string | null;
  email: string | null;
  phone: string | null;
  tool_name: string | null;
  campaign_name: string | null;
  external_candidate_id: string | null;
  surveymonkey_recipient_id: string | null;
  surveymonkey_response_id: string | null;
  surveymonkey_response_status: string | null;
  survey_responded_at: string | null;
  survey_status: SurveyStatus;
  call_status: CallStatus;
  opted_out_call: boolean;
  opted_out_email: boolean;
  created_at: string;
  updated_at: string;
}

export interface CampaignCandidateCreate {
  candidate_name?: string | null;
  email?: string | null;
  phone?: string | null;
  tool_name?: AssessmentTool | null;
  campaign_name?: string | null;
  external_candidate_id?: string | null;
  opted_out_call?: boolean;
  opted_out_email?: boolean;
}

export interface CampaignCandidateSurveyStatusUpdate {
  survey_status: SurveyStatus;
}

// ---------------------------------------------------------------------------
// SurveyMonkey survey workflow
// ---------------------------------------------------------------------------

export interface SurveyMonkeyTemplateRead {
  id: string;
  title: string;
  nickname: string | null;
}

export interface CampaignSurveyTemplateCreate {
  template_survey_id?: string | null;
}

export interface CampaignSurveyMessageCreate {
  subject: string;
  collector_id?: string | null;
  collector_name?: string | null;
  body?: string | null; // if provided, must include the 4 required placeholders
}

export interface CampaignSurveyMessageCreateResponse {
  campaign_id: string;
  survey_id: string;
  collector_id: string;
  message_id: string;
  subject: string;
}

export interface CampaignSurveyRecipientsPrepare {
  message_id: string;
  collector_id?: string | null;
}

export interface CampaignSurveyRecipientsPrepareResponse {
  campaign_id: string;
  collector_id: string;
  message_id: string;
  eligible_candidates: number;
  skipped_candidates: number;
  prepared_recipients: number;
}

export interface CampaignSurveyMessageSend {
  message_id: string;
  confirm_send: string; // must be exactly "SEND_SURVEY_EMAILS"
  collector_id?: string | null;
}

export interface CampaignSurveyMessageSendResponse {
  campaign_id: string;
  collector_id: string;
  message_id: string;
  sent: boolean;
  updated_candidates: number;
}

export interface CampaignSurveySyncResponse {
  total_recipients: number;
  completed: number;
  partial: number;
  non_responders: number;
  updated_candidates: number;
}

/** The exact confirmation phrase POST .../survey/message/send requires. */
export const SURVEY_SEND_CONFIRMATION = "SEND_SURVEY_EMAILS";

// ---------------------------------------------------------------------------
// Outbound call attempts
// ---------------------------------------------------------------------------

export interface OutboundCallAttemptRead {
  id: string;
  campaign_id: string;
  candidate_id: string;
  candidate_name: string | null;
  candidate_email: string | null;
  campaign_name: string | null;
  tool_name: string | null;
  phone: string;
  attempt_number: number;
  status: OutboundCallAttemptStatus;
  disposition: string | null;
  elevenlabs_conversation_id: string | null;
  transcript: string | null;
  summary: string | null;
  recording_url: string | null;
  started_at: string | null;
  ended_at: string | null;
  created_at: string;
}

export interface OutboundCallAttemptStatusUpdate {
  status: OutboundCallAttemptStatus;
  disposition?: string | null;
  elevenlabs_conversation_id?: string | null;
  transcript?: string | null;
  summary?: string | null;
  recording_url?: string | null;
}

// ---------------------------------------------------------------------------
// Jobs (app/schemas/job.py)
// ---------------------------------------------------------------------------

export interface JobQueuedRead {
  job_id: string;
  status: string;
}

export interface JobStatusRead {
  job_id: string;
  status: string;
  created_at: string | null;
  enqueued_at: string | null;
  started_at: string | null;
  ended_at: string | null;
  result: Record<string, unknown> | null;
  error: string | null;
  error_traceback: string | null;
}

// ---------------------------------------------------------------------------
// Helpdesk reporting (app/services/helpdesk_reporting_service.py)
// ---------------------------------------------------------------------------

export interface HelpdeskSummary {
  total_tickets: number;
  action_counts: Record<string, number>;
  rule_counts: Record<string, number>;
  issue_category_counts: Record<string, number>;
  confidence_counts: Record<string, number>;
  kb_gap_count: number;
  sensitive_count: number;
  drafts_awaiting_execution: number;
  drafts_placed_on_zoho: number;
  automation_rate: number;
  category_action_mix: Record<string, Record<string, number>>;
}

// ---------------------------------------------------------------------------
// Health (app/api/routes/health.py)
// ---------------------------------------------------------------------------

export interface HealthResponse {
  status: string;
  environment: string;
}
