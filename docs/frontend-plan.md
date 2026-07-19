# Candidate Experience — Frontend Scaffold Plan

## What this is (and isn't)

This is a **standalone reference frontend** for the Candidate Experience API. Its job is to:

1. Let the team **visualize** what the service does.
2. Show the builder **exactly how to consume every endpoint** — request shapes, response shapes, loading/error/polling patterns — in real, working code.

It is **not** the final production UI. The real UI will be rebuilt inside the existing multi-service platform app (which already owns the database, authentication, and shell). This scaffold is a portable, self-contained reference the builder lifts patterns from.

Because of that, every decision below favors **clarity and portability** over cleverness.

## Architecture decisions (confirmed)

| Decision | Choice | Why |
|---|---|---|
| Framework | **Vite + React + TypeScript** | Client-only app consuming a REST API; no need for Next.js SSR/routing/server-components that would be stripped out when porting. TS types mirror the API shapes — the single biggest "help the builder understand" lever. |
| Where it lives | Standalone project (own `package.json`, own dev server) — **talks to the backend only over HTTP**, never imports backend code | It's a separate deployable, exactly like it'll be in the host app. |
| Auth | **Not implemented here.** One injection point in the API client where the host app's token will later attach. | Auth belongs to the multi-service host. Backend currently has none, so local dev needs no token. |
| API base URL | Single env var `VITE_API_BASE_URL` (local `http://localhost:8000`) | Change one value to point at production. No hardcoded/file-path URLs. |
| Data fetching | **TanStack Query (React Query)** | Standard for REST consumption; gives clean loading/error/caching and makes the async-job **polling** trivial — which this API needs (survey sync, outbound calls). Realistic pattern the host app likely uses too. |
| Routing | **React Router** | Simple client routing between screens. |
| Styling | **Tailwind CSS** | Ubiquitous, fast, ports cleanly. Swappable — it's only presentation. |
| HTTP | Thin typed **`fetch` wrapper** (no axios) | One small module: base URL + the auth-injection point + JSON/error handling. Minimal deps, maximum clarity. |

All of these except the first two are **swappable presentation/tooling choices** — if the host app standardizes on something else (e.g. a different data-fetching lib or styling system), the builder swaps them; the API client and type layer stay identical.

## The one file that matters most

`src/lib/apiClient.ts` — the single choke point for every request. This is where:

- `VITE_API_BASE_URL` is read (localhost now, domain later).
- The **auth header injection point** lives (a no-op now; returns the host token later).
- JSON encoding, error handling, and typed responses are centralized.

Everything else calls typed functions that go through this. Change the base URL or add auth in **one place**.

```ts
// shape only — real version comes in Part 2
const BASE_URL = import.meta.env.VITE_API_BASE_URL;

function authHeader(): Record<string, string> {
  // Scaffold: no auth (backend has none). Host app replaces this with its token.
  // return { Authorization: `Bearer ${getHostToken()}` };
  return {};
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    method,
    headers: { "Content-Type": "application/json", ...authHeader() },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw await toApiError(res); // typed, includes status + detail
  return res.status === 204 ? (undefined as T) : res.json();
}
```

## Screen → endpoint coverage map

Every endpoint from `docs/api-reference.md` is accounted for, so nothing is missed. (Provider-facing webhooks are intentionally excluded — they're called by Twilio/ElevenLabs/Zoho, not a UI.)

| Screen | Endpoints consumed |
|---|---|
| **App shell / health badge** | `GET /health` |
| **Campaigns list** | `GET /campaigns`, `POST /campaigns` |
| **Campaign detail — Summary tab** | `GET /campaigns/{id}`, `GET /campaigns/{id}/summary`, `PATCH /campaigns/{id}/status` |
| **Campaign detail — Candidates tab** | `GET /campaigns/{id}/candidates`, `POST /campaigns/{id}/candidates`, `POST /campaigns/{id}/candidates/upload`, `GET /campaigns/{id}/non-responders`, `PATCH /campaigns/{id}/candidates/{cid}/survey-status` |
| **Campaign detail — Survey tab** | `GET /campaigns/survey/templates`, `POST /campaigns/{id}/survey/create-from-template`, `POST /campaigns/{id}/survey/message`, `POST /campaigns/{id}/survey/recipients/prepare`, `POST /campaigns/{id}/survey/message/send`, `POST /campaigns/{id}/survey/sync-responses`, `POST /campaigns/{id}/survey/sync-responses/jobs` |
| **Campaign detail — Outbound tab** | `POST /campaigns/{id}/outbound/build-queue`, `POST /campaigns/{id}/outbound/retry-queue`, `GET /campaigns/{id}/outbound/attempts`, `GET /campaigns/{id}/outbound/attempts/next`, `PATCH /campaigns/{id}/outbound/attempts/{aid}/status`, `POST /campaigns/{id}/outbound/execute-next/jobs` |
| **Call Records** | `GET /call-records`, `GET /call-records/{external_call_id}`, `POST /call-records` |
| **Helpdesk dashboard** | `GET /helpdesk/reports/summary` |
| **Jobs (shared component)** | `GET /jobs/{job_id}` — polled after any `202` |
| *Excluded (provider-facing)* | `POST /voice/*`, `POST /webhooks/voice-agent/*`, `POST /helpdesk/webhooks/*` |

## Build sequence (step by step — one part, then stop)

Each part is self-contained and ends at a point you can run and see working. We do one, stop, and only continue on your say-so.

- **Part 1 — Scaffold & shell.** Vite+React+TS project, Tailwind, React Query provider, router, `.env` with `VITE_API_BASE_URL`, folder structure, and an app shell with nav between the two systems + a live **health badge** proving the API connection works. *Verify: dev server runs, health badge goes green against localhost:8000.*
- **Part 2 — API client & types.** `apiClient.ts` (base URL + auth injection point + error handling), a `types.ts` mirroring the backend schemas, typed endpoint functions grouped by system, and the `useJob` polling hook. *This is the reference core the builder studies.*
- **Part 3 — Campaigns list + create.**
- **Part 4 — Campaign detail: Summary + Candidates tabs** (incl. file upload).
- **Part 5 — Campaign detail: Survey workflow** (the 4-step SurveyMonkey sequence with the `SEND_SURVEY_EMAILS` confirm gate + async sync with live polling).
- **Part 6 — Campaign detail: Outbound calls** (build/retry queue, attempts list, execute-next job).
- **Part 7 — Call Records** (list + filters + detail).
- **Part 8 — Helpdesk dashboard** (stat tiles + charts over the reporting summary).
- **Part 9 — Polish & handoff.** Consistent loading/empty/error states, a `README.md` for the builder (how to run, how to point at prod, **where/how to wire the host app's auth**), `.env.example`, and "how to port into the multi-service app" notes.

## Open decisions for you before Part 1

1. **Location:** put the scaffold in a `frontend/` subfolder of this repo (simplest for our session; it's still fully standalone — own package.json, HTTP-only), or a sibling directory outside this repo? *Recommendation: `frontend/` subfolder — easy to find, trivially liftable later.*
2. **Stack confirmation:** OK with Vite + React + TS + React Query + React Router + Tailwind? Any of the last four can be swapped to match your host app's conventions if you already know them.
