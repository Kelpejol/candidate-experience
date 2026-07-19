/**
 * Single source of truth for the API base URL.
 *
 * Local dev points at the FastAPI backend on :8000. To target a deployed
 * backend, change VITE_API_BASE_URL in `.env` — nothing else in the app
 * references a URL directly.
 *
 * Vite only exposes env vars prefixed with `VITE_` to the browser bundle.
 */
export const API_BASE_URL: string =
  import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";
