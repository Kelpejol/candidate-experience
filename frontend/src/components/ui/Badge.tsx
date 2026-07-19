import type { ReactNode } from "react";

import { humanize } from "../../lib/format";

export type Tone = "gray" | "blue" | "green" | "amber" | "red" | "purple";

const TONES: Record<Tone, string> = {
  gray: "bg-slate-100 text-slate-700",
  blue: "bg-blue-100 text-blue-700",
  green: "bg-emerald-100 text-emerald-700",
  amber: "bg-amber-100 text-amber-800",
  red: "bg-red-100 text-red-700",
  purple: "bg-purple-100 text-purple-700",
};

export function Badge({
  tone = "gray",
  children,
}: {
  tone?: Tone;
  children: ReactNode;
}) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${TONES[tone]}`}
    >
      {children}
    </span>
  );
}

// Status → tone maps. Keeping these next to the Badge so every screen renders
// the same status the same color.

const CAMPAIGN_STATUS_TONE: Record<string, Tone> = {
  draft: "gray",
  uploaded: "blue",
  survey_sending: "amber",
  survey_sent: "blue",
  waiting_for_responses: "amber",
  non_response_checking: "amber",
  outbound_ready: "blue",
  outbound_calling: "amber",
  completed: "green",
  failed: "red",
};

export function CampaignStatusBadge({ status }: { status: string }) {
  return (
    <Badge tone={CAMPAIGN_STATUS_TONE[status] ?? "gray"}>{humanize(status)}</Badge>
  );
}

const SURVEY_STATUS_TONE: Record<string, Tone> = {
  not_sent: "gray",
  sent: "blue",
  responded: "green",
  partial_response: "amber",
  non_responder: "amber",
  excluded_opt_out: "gray",
  failed: "red",
};

export function SurveyStatusBadge({ status }: { status: string }) {
  return (
    <Badge tone={SURVEY_STATUS_TONE[status] ?? "gray"}>{humanize(status)}</Badge>
  );
}

const CALL_STATUS_TONE: Record<string, Tone> = {
  not_queued: "gray",
  queued: "blue",
  calling: "amber",
  answered: "green",
  responded_by_call: "green",
  no_answer: "amber",
  busy: "amber",
  voicemail: "amber",
  failed: "red",
  opted_out: "gray",
  handed_off_to_human: "purple",
};

export function CallStatusBadge({ status }: { status: string }) {
  return (
    <Badge tone={CALL_STATUS_TONE[status] ?? "gray"}>{humanize(status)}</Badge>
  );
}

const OUTBOUND_ATTEMPT_STATUS_TONE: Record<string, Tone> = {
  queued: "blue",
  calling: "amber",
  answered: "green",
  responded_by_call: "green",
  no_answer: "amber",
  busy: "amber",
  voicemail: "amber",
  failed: "red",
  opted_out: "gray",
  handed_off_to_human: "purple",
};

export function AttemptStatusBadge({ status }: { status: string }) {
  return (
    <Badge tone={OUTBOUND_ATTEMPT_STATUS_TONE[status] ?? "gray"}>
      {humanize(status)}
    </Badge>
  );
}

const CALL_DISPOSITION_TONE: Record<string, Tone> = {
  answered_by_ai: "green",
  handed_off_to_human: "purple",
  handoff_failed: "red",
  missed: "gray",
  voicemail: "amber",
  no_answer: "amber",
  opted_out: "gray",
  failed: "red",
};

export function CallDispositionBadge({ disposition }: { disposition: string }) {
  return (
    <Badge tone={CALL_DISPOSITION_TONE[disposition] ?? "gray"}>
      {humanize(disposition)}
    </Badge>
  );
}

export function DirectionBadge({ direction }: { direction: string }) {
  return (
    <Badge tone={direction === "inbound" ? "blue" : "purple"}>
      {humanize(direction)}
    </Badge>
  );
}
