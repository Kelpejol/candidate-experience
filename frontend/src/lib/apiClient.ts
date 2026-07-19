import { API_BASE_URL } from "./config";

/**
 * The single choke point for every backend request.
 *
 *  - Reads the base URL from config (localhost now, deployed domain later).
 *  - `authHeaders()` is the ONE place to attach an auth token when this
 *    scaffold is ported into the multi-service host app. It is empty now
 *    because the scaffold's backend has no authentication.
 *  - Normalizes FastAPI's two error shapes into a single readable `ApiError`.
 *
 * Every typed endpoint function in `src/api/*` goes through here, so changing
 * the base URL or wiring in auth is a one-line change confined to this file.
 */

/** One item from a FastAPI 422 validation-error `detail` array. */
export interface ValidationErrorItem {
  loc: (string | number)[];
  msg: string;
  type: string;
}

/**
 * Thrown for any non-2xx response. `.message` is always human-readable;
 * `.status` and `.detail` carry the raw specifics for finer handling.
 */
export class ApiError extends Error {
  readonly status: number;
  readonly detail: string | ValidationErrorItem[] | undefined;

  constructor(
    status: number,
    detail: string | ValidationErrorItem[] | undefined,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

/**
 * ⭐ AUTH INJECTION POINT ⭐
 *
 * When this scaffold is ported into the multi-service host app, return the
 * platform's token here — every request picks it up automatically:
 *
 *   return { Authorization: `Bearer ${getHostToken()}` };
 *
 * The scaffold's backend has no auth, so this is intentionally empty.
 */
function authHeaders(): Record<string, string> {
  return {};
}

/** Convert FastAPI's `{detail}` (string or 422 array) into a readable ApiError. */
function toApiError(status: number, body: unknown): ApiError {
  const detail = (body as { detail?: string | ValidationErrorItem[] })?.detail;

  let message: string;
  if (typeof detail === "string") {
    message = detail;
  } else if (Array.isArray(detail)) {
    // 422 validation errors: join each field path with its message.
    message = detail
      .map((item) => `${item.loc.filter((p) => p !== "body").join(".")}: ${item.msg}`)
      .join("; ");
  } else {
    message = `Request failed with status ${status}`;
  }

  return new ApiError(status, detail, message);
}

async function parseBody(res: Response): Promise<unknown> {
  if (res.status === 204) return undefined;
  const text = await res.text();
  if (!text) return undefined;
  try {
    return JSON.parse(text);
  } catch {
    return text; // non-JSON (e.g. the /voice TwiML endpoints return XML)
  }
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    method,
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const parsed = await parseBody(res);
  if (!res.ok) throw toApiError(res.status, parsed);
  return parsed as T;
}

/**
 * Multipart form POST (file uploads). Note: we deliberately do NOT set a
 * Content-Type header — the browser must set it to `multipart/form-data`
 * with the correct boundary itself.
 */
async function requestForm<T>(path: string, form: FormData): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    headers: { ...authHeaders() },
    body: form,
  });
  const parsed = await parseBody(res);
  if (!res.ok) throw toApiError(res.status, parsed);
  return parsed as T;
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
  patch: <T>(path: string, body?: unknown) => request<T>("PATCH", path, body),
  postForm: <T>(path: string, form: FormData) => requestForm<T>(path, form),
};

/**
 * Build a `?a=1&b=2` query string from a params object, skipping null/
 * undefined values. Returns `""` when nothing is set.
 */
export function buildQuery(
  params: Record<string, string | number | boolean | null | undefined>,
): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined) search.set(key, String(value));
  }
  const str = search.toString();
  return str ? `?${str}` : "";
}
