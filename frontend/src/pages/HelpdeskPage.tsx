import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { getHelpdeskAIActions, getHelpdeskSummary } from "../api/helpdesk";
import { Badge } from "../components/ui/Badge";
import { Card } from "../components/ui/Card";
import { ErrorState, LoadingState } from "../components/ui/QueryStates";
import { StatTile } from "../components/ui/StatTile";
import { formatDateTime, humanize } from "../lib/format";
import type { HelpdeskAIActionRead, HelpdeskSummary } from "../lib/types";

// Palette (validated via the dataviz skill's validator, light mode):
// - Action mix = categorical identity (3 hues, fixed order, legend + counts).
// - Magnitude bars = single sequential hue (blue); identity is on the label.
const ACTION_ORDER = ["auto_reply", "draft_reply", "ask_clarification", "request_attachment", "route_to_human", "tag_only"];
const ACTION_COLORS: Record<string, string> = {
  draft_reply: "#2a78d6",
  auto_reply: "#087f8c",
  ask_clarification: "#a26800",
  request_attachment: "#6958a8",
  route_to_human: "#008300",
  tag_only: "#e87ba4",
};
const MAGNITUDE_HUE = "#2a78d6";

/** Horizontal magnitude bars, sorted descending, single hue + count labels. */
function MagnitudeBars({ data }: { data: Record<string, number> }) {
  const entries = Object.entries(data).sort((a, b) => b[1] - a[1]);
  const max = entries.length ? entries[0][1] : 0;
  if (entries.length === 0)
    return <p className="text-sm text-slate-400">No data yet</p>;

  return (
    <div className="space-y-2">
      {entries.map(([key, count]) => (
        <div key={key} className="flex items-center gap-3">
          <span
            className="w-44 shrink-0 truncate text-sm text-slate-600"
            title={humanize(key)}
          >
            {humanize(key)}
          </span>
          <div className="flex-1">
            <div
              className="h-2.5 rounded"
              style={{
                width: `${max ? (count / max) * 100 : 0}%`,
                minWidth: count > 0 ? 6 : 0,
                backgroundColor: MAGNITUDE_HUE,
              }}
            />
          </div>
          <span className="w-8 text-right text-sm font-medium tabular-nums text-slate-900">
            {count}
          </span>
        </div>
      ))}
    </div>
  );
}

/** Action mix as a single 100% stacked bar with a labelled, counted legend. */
function ActionMixBar({ summary }: { summary: HelpdeskSummary }) {
  const counts = summary.action_counts;
  const keys = [
    ...ACTION_ORDER.filter((k) => k in counts),
    ...Object.keys(counts).filter((k) => !ACTION_ORDER.includes(k)),
  ];
  const total = summary.total_tickets || keys.reduce((s, k) => s + counts[k], 0);

  if (total === 0) return <p className="text-sm text-slate-400">No tickets yet</p>;

  return (
    <div>
      <div className="flex h-4 w-full overflow-hidden rounded">
        {keys.map((key) => (
          <div
            key={key}
            className="border-r-2 border-white last:border-r-0"
            style={{
              width: `${(counts[key] / total) * 100}%`,
              backgroundColor: ACTION_COLORS[key] ?? "#94a3b8",
            }}
            title={`${humanize(key)}: ${counts[key]}`}
          />
        ))}
      </div>
      <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1.5">
        {keys.map((key) => (
          <span key={key} className="flex items-center gap-1.5 text-sm">
            <span
              className="h-3 w-3 rounded-sm"
              style={{ backgroundColor: ACTION_COLORS[key] ?? "#94a3b8" }}
            />
            <span className="text-slate-600">{humanize(key)}</span>
            <span className="font-medium tabular-nums text-slate-900">
              {counts[key]}
            </span>
          </span>
        ))}
      </div>
    </div>
  );
}

/** Per-category action matrix — a table (the honest form for a 2-D count grid). */
function CategoryMatrix({ summary }: { summary: HelpdeskSummary }) {
  const categories = Object.keys(summary.category_action_mix);
  if (categories.length === 0)
    return <p className="text-sm text-slate-400">No data yet</p>;

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500">
          <tr>
            <th className="py-2 pr-4 font-medium">Category</th>
            {ACTION_ORDER.map((action) => (
              <th key={action} className="px-3 py-2 text-right font-medium">
                {humanize(action)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {categories.map((category) => {
            const mix = summary.category_action_mix[category];
            return (
              <tr key={category}>
                <td className="py-2 pr-4 text-slate-700">
                  {humanize(category)}
                </td>
                {ACTION_ORDER.map((action) => (
                  <td
                    key={action}
                    className="px-3 py-2 text-right tabular-nums text-slate-900"
                  >
                    {mix[action] ?? "—"}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function SectionCard({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}) {
  return (
    <Card className="p-5">
      <p className="text-sm font-semibold text-slate-900">{title}</p>
      {subtitle && <p className="mb-4 mt-0.5 text-xs text-slate-500">{subtitle}</p>}
      <div className={subtitle ? "" : "mt-4"}>{children}</div>
    </Card>
  );
}

// --- Recent AI decisions (the review view) ---------------------------------

type ActionParams = {
  action_type?: string;
  only_drafts?: boolean;
  limit?: number;
};

const ACTION_TONE: Record<string, "blue" | "green" | "gray"> = {
  draft_reply: "blue",
  auto_reply: "green",
  ask_clarification: "blue",
  request_attachment: "blue",
  route_to_human: "green",
  tag_only: "gray",
};

const DECISION_FILTERS: { key: string; label: string; params: ActionParams }[] = [
  { key: "all", label: "All", params: { limit: 100 } },
  { key: "drafts", label: "Drafts", params: { only_drafts: true, limit: 100 } },
  { key: "clarifications", label: "Clarifications", params: { action_type: "ask_clarification", limit: 100 } },
  { key: "attachments", label: "Attachment Requests", params: { action_type: "request_attachment", limit: 100 } },
  { key: "routed", label: "Routed", params: { action_type: "route_to_human", limit: 100 } },
  { key: "tagged", label: "Tagged", params: { action_type: "tag_only", limit: 100 } },
];

/** One decision, with an expandable draft. */
function DecisionRow({ action }: { action: HelpdeskAIActionRead }) {
  const [open, setOpen] = useState(false);
  return (
    <li className="py-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={ACTION_TONE[action.action_type] ?? "gray"}>
          {humanize(action.action_type)}
        </Badge>
        {action.issue_category && (
          <Badge tone="gray">{humanize(action.issue_category)}</Badge>
        )}
        {action.sensitivity_detected && <Badge tone="red">Sensitive</Badge>}
        {action.grounding_status === "missing" && <Badge tone="amber">KB gap</Badge>}
        {action.confidence_label && (
          <span className="text-xs text-slate-500">
            {humanize(action.confidence_label)} confidence
          </span>
        )}
        <span className="ml-auto text-xs text-slate-400">
          {formatDateTime(action.created_at)}
        </span>
      </div>
      <p className="mt-1.5 text-sm font-medium text-slate-800">
        {action.subject || "(no subject)"}
      </p>
      <p className="text-xs text-slate-500">
        {action.channel ? `${action.channel} · ` : ""}
        {action.candidate_email ?? "unknown sender"}
      </p>
      {action.reason && (
        <p className="mt-1 text-xs italic text-slate-500">{action.reason}</p>
      )}
      {action.draft_text && (
        <div className="mt-2">
          <button
            onClick={() => setOpen((v) => !v)}
            className="text-xs font-medium text-blue-700 hover:underline"
          >
            {open ? "Hide draft" : "Show draft"}
          </button>
          {open && (
            <pre className="mt-2 whitespace-pre-wrap rounded-md bg-slate-50 p-3 text-sm text-slate-800">
              {action.draft_text}
            </pre>
          )}
        </div>
      )}
    </li>
  );
}

function RecentDecisions() {
  const [filter, setFilter] = useState("all");
  const active =
    DECISION_FILTERS.find((f) => f.key === filter) ?? DECISION_FILTERS[0];
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ["helpdesk-actions", filter],
    queryFn: () => getHelpdeskAIActions(active.params),
  });

  return (
    <SectionCard
      title="Recent AI decisions"
      subtitle="What the AI decided on each ticket — and the draft it wrote."
    >
      <div className="mb-4 flex flex-wrap gap-2">
        {DECISION_FILTERS.map((f) => (
          <button
            key={f.key}
            onClick={() => setFilter(f.key)}
            className={`rounded-full px-3 py-1 text-xs font-medium ${
              filter === f.key
                ? "bg-slate-800 text-white"
                : "bg-slate-100 text-slate-600 hover:bg-slate-200"
            }`}
          >
            {f.label}
          </button>
        ))}
      </div>
      {isLoading && <LoadingState label="Loading decisions…" />}
      {isError && <ErrorState error={error} onRetry={() => refetch()} />}
      {data && data.length === 0 && (
        <p className="text-sm text-slate-400">No decisions yet.</p>
      )}
      {data && data.length > 0 && (
        <ul className="divide-y divide-slate-100">
          {data.map((a) => (
            <DecisionRow key={a.id} action={a} />
          ))}
        </ul>
      )}
    </SectionCard>
  );
}

export function HelpdeskPage() {
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ["helpdesk-summary"],
    queryFn: getHelpdeskSummary,
  });

  return (
    <div className="mx-auto max-w-5xl">
      <header className="mb-6">
        <h2 className="text-xl font-semibold text-slate-900">
          Helpdesk Dashboard
        </h2>
        <p className="mt-1 text-sm text-slate-600">
          AI automation health over the Zoho Desk ticket decision audit trail.
        </p>
      </header>

      {isLoading && <LoadingState label="Loading summary…" />}
      {isError && <ErrorState error={error} onRetry={() => refetch()} />}

      {data && (
        <div className="space-y-6">
          {/* Headline stat tiles */}
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
            <StatTile label="Total tickets" value={data.total_tickets} />
            <StatTile
              label="Automation rate"
              value={`${Math.round(data.automation_rate * 100)}%`}
              hint="drafted or tagged"
            />
            <StatTile label="Sensitive" value={data.sensitive_count} />
            <StatTile label="KB gaps" value={data.kb_gap_count} />
            <StatTile
              label="Drafts to review"
              value={data.drafts_awaiting_execution}
            />
            <StatTile label="Drafts on Zoho" value={data.drafts_placed_on_zoho} />
          </div>

          {/* Action mix */}
          <SectionCard
            title="Action mix"
            subtitle="What the AI did with each ticket."
          >
            <ActionMixBar summary={data} />
          </SectionCard>

          {/* Categories + rules side by side */}
          <div className="grid gap-6 lg:grid-cols-2">
            <SectionCard
              title="Issue categories"
              subtitle="Ticket volume by classified category."
            >
              <MagnitudeBars data={data.issue_category_counts} />
            </SectionCard>
            <SectionCard
              title="Decision rules"
              subtitle="Which rule decided each ticket — the 'why'."
            >
              <MagnitudeBars data={data.rule_counts} />
            </SectionCard>
          </div>

          {/* Confidence + matrix */}
          <div className="grid gap-6 lg:grid-cols-2">
            <SectionCard
              title="Classifier confidence"
              subtitle="Self-reported confidence of the AI classifier."
            >
              <MagnitudeBars data={data.confidence_counts} />
            </SectionCard>
            <SectionCard
              title="Category → action"
              subtitle="Evidence base for later auto-send promotion."
            >
              <CategoryMatrix summary={data} />
            </SectionCard>
          </div>
        </div>
      )}

      {/* Per-ticket decisions + drafts — independent of the summary query. */}
      <div className="mt-6">
        <RecentDecisions />
      </div>
    </div>
  );
}
