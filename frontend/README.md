# Candidate Experience — Frontend Scaffold

A **standalone reference frontend** for the Candidate Experience API. It exists
to (1) let people *see* what the service does, and (2) show a builder *exactly*
how to consume every endpoint — request shapes, response shapes, loading/error/
polling patterns — in real, working code.

It is **not** the final production UI. The real UI will be rebuilt inside the
existing multi-service platform app (which already owns the database, auth, and
app shell). This scaffold is a portable, self-contained reference to lift
patterns from. See `../docs/frontend-plan.md` for the plan it was built against
and `../docs/api-reference.md` for the full endpoint documentation.

---

## 1. Quick start

You need **two processes running**: the backend API and this frontend.

**Backend** (from the repo root, one level up):

```bash
cd ..
PYTHONPATH=. .venv/bin/uvicorn main:app --port 8000
```

**Frontend** (from this `frontend/` folder):

```bash
npm install        # first time only
npm run dev        # starts Vite on http://localhost:5173
```

Open **http://localhost:5173**. The header shows a **health badge** — green
means the frontend is talking to the backend, red means it can't reach it. If
it's red, the backend isn't running (or CORS isn't allowing your origin — see §3).

Other scripts:

- `npm run build` — type-checks (`tsc`) and produces a production bundle in `dist/`.
- `npm run preview` — serves the built bundle locally.

---

## 2. How it talks to the backend (one env var)

Every request goes through a single base URL, read from `VITE_API_BASE_URL` in
`.env`:

```
VITE_API_BASE_URL=http://localhost:8000
```

To point the whole app at a deployed backend, **change that one value** and
rebuild. Nothing else in the codebase references a URL directly. (Vite only
exposes env vars prefixed with `VITE_` to the browser.)

`.env` is gitignored; `.env.example` is the committed template.

---

## 3. Authentication — where it plugs in

**This scaffold has no authentication of its own, by design.** Auth belongs to
the multi-service host app that this will be ported into. The scaffold's backend
currently has no auth either, so locally you call it directly.

There is exactly **one place** to wire auth in — the `authHeaders()` function in
`src/lib/apiClient.ts`:

```ts
function authHeaders(): Record<string, string> {
  return {};   // scaffold: no auth
  // host app: return { Authorization: `Bearer ${getHostToken()}` };
}
```

Every request in the entire app flows through this function. When you port the
code into the host app, return the platform's token here and **all** requests
pick it up — no other change needed.

> **CORS note:** because the frontend (`:5173`) and backend (`:8000`) are
> different origins, the backend must send CORS headers or the browser blocks
> every call. This was added to the backend (`CORS_ALLOW_ORIGINS` setting +
> middleware in `main.py`). Add the deployed UI's origin there in production.

---

## 4. How the code is organized (the layers)

The app is built in clean layers. Data flows **down** this list; each layer only
knows about the one below it:

```
  pages/            ← full screens, one per route (what the user sees)
  components/       ← reusable pieces (ui kit + feature components)
  hooks/            ← reusable behavior (useJob polling)
  api/              ← one typed function per endpoint
  lib/apiClient.ts  ← the single fetch choke point (base URL + auth + errors)
  lib/types.ts      ← TypeScript mirror of the backend schemas
  lib/config.ts     ← the base URL
```

**The golden rule:** UI code (pages/components) never calls `fetch` or builds a
URL. It calls a typed function from `api/`, which goes through `apiClient`. So
there's one place for the base URL, one place for auth, one place for error
handling.

### The request lifecycle, end to end

Take the Campaigns list as the example:

1. `CampaignsPage` calls React Query's `useQuery({ queryFn: () => listCampaigns() })`.
2. `listCampaigns()` (in `api/campaigns.ts`) calls `api.get<CampaignRead[]>("/campaigns")`.
3. `api.get` (in `lib/apiClient.ts`) prepends `VITE_API_BASE_URL`, adds
   `authHeaders()`, does the `fetch`, and on a non-2xx throws a readable `ApiError`.
4. React Query hands the page `data` / `isLoading` / `isError`, which the page
   renders as a table / spinner / error box.

Every screen is a variation on this. Once you understand it once, you understand
all of them.

---

## 5. The key patterns (and why)

### React Query for all data fetching

Every server call uses `@tanstack/react-query` (`useQuery` for reads,
`useMutation` for writes). It gives you loading/error/caching for free, and —
importantly for this API — clean **polling** (see `useJob`). After a write, we
call `queryClient.invalidateQueries(...)` so the affected lists refetch and the
UI updates automatically (e.g. creating a campaign makes it appear in the list).

### The typed API client (`lib/apiClient.ts`)

One small module wrapping `fetch`. It owns:

- the base URL (from config),
- the **auth injection point** (`authHeaders()`),
- JSON encoding + a `postForm` variant for file uploads (which deliberately
  omits `Content-Type` so the browser sets the multipart boundary),
- **error normalization**: FastAPI returns errors two ways — a plain
  `{"detail": "..."}` string, or a 422 validation array `{"detail": [{loc, msg}]}`.
  Both are turned into a single `ApiError` with a readable `.message`, plus
  `.status` and `.detail` for finer handling.

### `lib/types.ts` — the schemas, mirrored

One TypeScript interface per backend Pydantic schema. Two conventions worth
knowing:

- **Read types use `| null`, Create/Update types use `?`.** The backend
  serializes absent optionals as JSON `null` in responses, but a client omits
  them in requests — so the types reflect reality on each side.
- **Enums are `const` arrays → derived union types** (e.g. `CAMPAIGN_STATUSES`,
  `ASSESSMENT_TOOLS`). This gives the UI *both* a compile-time type *and* an
  iterable list to build dropdowns from — a status `<select>` can't drift out of
  sync with what the API accepts.

### `hooks/useJob.ts` — polling for async endpoints

Some endpoints don't do the work immediately — they return `202` + a `job_id`
and run in the background (survey response sync, placing outbound calls). The
pattern:

```ts
const [jobId, setJobId] = useState<string | null>(null);
const enqueue = useMutation({ mutationFn: () => enqueueSurveySyncJob(id),
                              onSuccess: (r) => setJobId(r.job_id) });
const job = useJob(jobId);   // polls GET /jobs/{id} every 1.5s, auto-stops when done
// job.isPolling / job.isFinished / job.isFailed / job.data.result
```

`useJob` polls until the job reaches a terminal state and then stops on its own.
The Survey tab's sync panel and the Outbound tab's "execute next" both use it —
they're the reference examples.

### The reusable UI kit (`components/ui/`)

Every screen composes from the same primitives, so the whole app stays visually
consistent and no screen re-styles from scratch:

| Component | Role |
|---|---|
| `Button` | variants: primary / secondary / danger / ghost |
| `Card` | bordered white surface |
| `Badge` + status badges | colored status pills (campaign, survey, call, attempt, disposition, direction) with fixed status→color maps |
| `Modal` | dialog (backdrop + Escape to close) |
| `FormField` + `inputClass` | labelled inputs with shared styling |
| `Spinner` | inline loading indicator |
| `QueryStates` | `LoadingState` / `ErrorState` (with retry) / `EmptyState` |
| `StatTile` | a headline number |

---

## 6. Screen-by-screen walkthrough

The sidebar groups screens by the **two independent systems** the backend serves
(they share no data by design).

### Calling Agent → Campaigns  (`pages/CampaignsPage.tsx`)

The list of survey campaigns. `GET /campaigns` renders the table (status badges,
tool, survey-linked indicator, created date). **"+ New campaign"** opens a modal
(`CreateCampaignModal`) that `POST`s a campaign and invalidates the list so it
appears immediately. Click any row to open its detail.

### Campaign detail  (`pages/CampaignDetailPage.tsx`)

Fetches one campaign (`GET /campaigns/{id}`) and renders a tabbed view:

- **Summary** (`CampaignSummaryTab`) — meta grid (tool, wait window, survey/
  collector linkage, timestamps), a **lifecycle-status changer**
  (`PATCH /campaigns/{id}/status`), and live aggregate stats
  (`GET /campaigns/{id}/summary`) shown as a total tile + three count breakdowns.
- **Candidates** (`CampaignCandidatesTab`) — paginated candidate table with
  survey/call status badges. A **"Non-responders only"** toggle swaps to
  `GET /campaigns/{id}/non-responders`. Two add paths: **"+ Add candidate"**
  (`AddCandidateModal`, single, typed) and **"Upload file"**
  (`UploadCandidatesModal`, CSV/XLSX bulk → `.../candidates/upload`, shows how
  many were added). Both invalidate the list *and* the summary.
- **Survey** (`CampaignSurveyTab`) — a guided **4-step stepper** for the
  SurveyMonkey flow: (1) create survey from template, (2) create the email
  message, (3) prepare recipients, (4) **send** — the send is irreversible, so
  it's gated behind a confirmation modal, and the required `SEND_SURVEY_EMAILS`
  phrase is baked in from a constant. Below the stepper, a **sync panel** uses
  `useJob` to enqueue a response sync and show the job progress + result live.
- **Outbound** (`CampaignOutboundTab`) — build the call queue, build a retry
  queue (with a max-attempts input), and **"Execute next call"** (an async job,
  polled with `useJob`). A "Next queued" indicator handles the 404 = "none
  queued" case gracefully. The attempts table has a per-row **"Set outcome
  (test)"** dropdown that `PATCH`es an attempt's status — this simulates the
  telephony provider's callback so you can walk the pipeline without a real call.

### Calling Agent → Call Records  (`pages/CallRecordsPage.tsx`)

Filterable log of every voice call. Filter by direction / disposition / tool
(dropdowns) and campaign name (text); `GET /call-records` with those params.
Click a row → `CallRecordDetailModal` fetches the full record
(`GET /call-records/{id}`) and shows candidate, timestamps, transcript, recording
link, and — only when the call was escalated — a **Human handoff** section.

### Helpdesk → Dashboard  (`pages/HelpdeskPage.tsx`)

Automation health over the Zoho ticket AI-decision audit trail
(`GET /helpdesk/reports/summary`). Six **stat tiles** (total tickets, automation
rate, sensitive, KB gaps, drafts to review, drafts on Zoho), an **action-mix**
stacked bar, **sorted magnitude bars** for issue categories and decision rules,
a **confidence** breakdown, and a **category→action matrix** table.

> **Why the Helpdesk screen is a dashboard, not a ticket workspace:** Zoho Desk
> is the system of record for tickets — officers read and reply to tickets *in
> Zoho*, not here. Our backend only stores the AI's *decision audit trail* (what
> the AI classified/decided per ticket) plus a lightweight ticket mirror. So this
> screen is deliberately a **monitoring/reporting** view of how well the
> automation is doing, not a place to work tickets. A ticket list/detail view
> would need new backend endpoints (there's no ticket-list API yet — see the note
> at the end of `../docs/api-reference.md`).

---

## 7. Porting into the multi-service host app

This scaffold is written to make that port mechanical:

1. **Auth** — implement `authHeaders()` in `lib/apiClient.ts` to return the
   host's token. Done — every call is now authenticated.
2. **Base URL** — set `VITE_API_BASE_URL` (or the host's equivalent) to the
   deployed API. If the host uses a different env system, change the one line in
   `lib/config.ts`.
3. **The `lib/`, `api/`, and `hooks/` layers move as-is** — they have no UI
   dependencies. This is the valuable, reusable core: the types, the client, the
   endpoint functions, the polling hook.
4. **`components/` and `pages/` are the reference** — reimplement them in the
   host's own component library / design system, using the scaffold versions as
   the spec for *what each screen does and which endpoints it calls*.
5. **Routing** — the host app owns routing; map its routes to these page
   components (or their reimplementations).
6. **Styling** — this uses Tailwind; if the host uses something else, the
   `components/ui/` kit is the only place styling lives, so swap those.

If the host app already uses React Query, the `api/` functions drop straight into
its query hooks. If it uses a different data layer, the `api/` functions are
still plain typed `fetch` wrappers you can call from anything.

---

## 8. Folder map

```
frontend/
├── .env                      # VITE_API_BASE_URL (gitignored)
├── .env.example              # committed template
├── src/
│   ├── main.tsx              # entry: React Query + Router providers
│   ├── App.tsx               # route table
│   ├── index.css             # Tailwind import
│   ├── lib/
│   │   ├── config.ts         # API base URL (the one env read)
│   │   ├── apiClient.ts      # ⭐ fetch choke point: base URL + auth + errors
│   │   ├── types.ts          # TS mirror of backend schemas + enum const-arrays
│   │   └── format.ts         # date/label formatting (UTC-safe)
│   ├── api/                  # one typed function per endpoint
│   │   ├── campaigns.ts      #   all /campaigns endpoints
│   │   ├── callRecords.ts    #   /call-records
│   │   ├── jobs.ts           #   /jobs/{id}
│   │   ├── helpdesk.ts       #   /helpdesk/reports/summary
│   │   └── health.ts         #   /health
│   ├── hooks/
│   │   └── useJob.ts         # async-job polling
│   ├── components/
│   │   ├── AppLayout.tsx     # sidebar + header shell
│   │   ├── HealthBadge.tsx   # live connection indicator
│   │   ├── ui/               # reusable kit (Button, Card, Badge, Modal, …)
│   │   ├── campaigns/        # campaign feature components (tabs, modals)
│   │   └── callRecords/      # call-record detail modal
│   └── pages/                # one component per route
│       ├── CampaignsPage.tsx
│       ├── CampaignDetailPage.tsx
│       ├── CallRecordsPage.tsx
│       └── HelpdeskPage.tsx
```

## 9. Stack

- **Vite + React + TypeScript** — client app; TS types mirror the API.
- **@tanstack/react-query** — data fetching, caching, polling.
- **react-router-dom** — client routing.
- **Tailwind CSS v4** — styling (swappable; confined to `components/ui/`).

All four are the reason the scaffold both teaches the API well and ports cleanly.
