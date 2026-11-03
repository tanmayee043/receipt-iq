/** The HTTP API (src/receipt_iq/api.py), typed by hand from its response models. */

export interface Provider {
  provider: string;
  local: boolean;
  fully_correct: number;
  total_correct: number;
  item_f1: number;
  catch_rate: number | null;
  false_alarm_rate: number | null;
  failure_rate: number;
  median_seconds: number;
  cost_per_1000: number;
  cost_note: string;
  meets_policy: boolean;
}

export interface Leaderboard {
  split: string;
  receipts: number;
  accuracy_target: number;
  max_seconds: number;
  routing_order: string[];
  providers: Provider[];
}

export interface Issue {
  code: string;
  message: string;
  expected: string;
  actual: string;
  location: string;
}

export interface Attempt {
  provider: string;
  outcome: string;
  reason: string;
  seconds: number;
}

export interface RoutedDocument {
  digest: string;
  status: string;
  provider: string | null;
  issues: Issue[];
  attempts: Attempt[];
  met_target: boolean | null;
  seconds: number | null;
  failure_kind: string | null;
  failure_reason: string | null;
  created_at: string;
}

export interface DocumentPage {
  items: RoutedDocument[];
  next_cursor: string | null;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

const UNREACHABLE = "Couldn't reach the server. Check your connection and try again.";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api${path}`, init);
  } catch {
    throw new ApiError(0, "unreachable", UNREACHABLE);
  }
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const error = (body as { error?: { code?: string; message?: string } } | null)?.error;
    throw new ApiError(
      response.status,
      error?.code ?? "unknown",
      error?.message ?? `The server answered ${response.status}.`,
    );
  }
  return body as T;
}

function post<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

export const api = {
  session: () => request<{ logged_in: boolean }>("/session"),
  login: (password: string) => post<{ logged_in: boolean }>("/login", { password }),
  logout: () => post<{ logged_in: boolean }>("/logout"),
  leaderboard: () => request<Leaderboard>("/leaderboard"),
  routingLog: (cursor: string | null) => {
    const query = cursor ? `&cursor=${encodeURIComponent(cursor)}` : "";
    return request<DocumentPage>(`/routing-log?limit=25${query}`);
  },
};
