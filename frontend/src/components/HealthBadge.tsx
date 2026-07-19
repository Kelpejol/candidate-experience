import { useQuery } from "@tanstack/react-query";

import { getHealth } from "../api/health";
import { API_BASE_URL } from "../lib/config";

/**
 * Live connection indicator in the header. Polls GET /health every 30s and
 * shows green (reachable) / red (unreachable).
 *
 * Now goes through the Part 2 API layer (`getHealth` → typed `api` client),
 * so it doubles as proof the whole client/types stack is wired correctly.
 */
export function HealthBadge() {
  const { data, isError, isLoading } = useQuery({
    queryKey: ["health"],
    queryFn: getHealth,
    refetchInterval: 30_000,
    retry: false,
  });

  const online = !isError && !!data;
  const label = isLoading
    ? "Connecting…"
    : online
      ? `API online · ${data.environment}`
      : "API offline";
  const dotColor = isLoading
    ? "bg-amber-400"
    : online
      ? "bg-emerald-500"
      : "bg-red-500";

  return (
    <div
      className="flex items-center gap-2 rounded-full border border-slate-200 bg-white px-3 py-1.5 text-sm text-slate-600"
      title={`${API_BASE_URL}/health`}
    >
      <span className={`h-2.5 w-2.5 rounded-full ${dotColor}`} />
      <span>{label}</span>
    </div>
  );
}
