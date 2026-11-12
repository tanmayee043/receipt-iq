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

export interface LineItem {
  name: string;
  quantity: string;
  unit_price: string | null;
  total_price: string;
  discount: string | null;
}

/** Money stays a string, so it's never rounded by floating point on the way through. */
export interface Receipt {
  vendor: string | null;
  currency: string | null;
  items: LineItem[];
  subtotal: string | null;
  discount: string | null;
  service_charge: string | null;
  tax: string | null;
  tip: string | null;
  total: string;
}

export interface RoutedDocument {
  digest: string;
  status: string;
  provider: string | null;
  receipt: Receipt | null;
  corrected_receipt: Receipt | null;
  confirmed_mismatch: boolean;
  issues: Issue[];
  attempts: Attempt[];
  met_target: boolean | null;
  seconds: number | null;
  failure_kind: string | null;
  failure_reason: string | null;
  created_at: string;
  updated_at: string;
}

export interface DocumentPage {
  items: RoutedDocument[];
  next_cursor: string | null;
}

/** A saved answer to a sample receipt, shown without calling any provider. */
export interface DemoSample {
  id: string;
  title: string;
  note: string;
  status: string;
  provider: string;
  receipt: Receipt;
  issues: Issue[];
  attempts: Attempt[];
  seconds: number;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: unknown;

  constructor(status: number, code: string, message: string, details?: unknown) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
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
    const error = (
      body as { error?: { code?: string; message?: string; details?: unknown } } | null
    )?.error;
    throw new ApiError(
      response.status,
      error?.code ?? "unknown",
      error?.message ?? `The server answered ${response.status}.`,
      error?.details,
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
  session: () => request<{ logged_in: boolean; demo_only?: boolean }>("/session"),
  login: (password: string) => post<{ logged_in: boolean }>("/login", { password }),
  logout: () => post<{ logged_in: boolean }>("/logout"),
  leaderboard: () => request<Leaderboard>("/leaderboard"),
  routingLog: (cursor: string | null) => {
    const query = cursor ? `&cursor=${encodeURIComponent(cursor)}` : "";
    return request<DocumentPage>(`/routing-log?limit=25${query}`);
  },
  reviewQueue: (cursor: string | null) => {
    const query = cursor ? `&cursor=${encodeURIComponent(cursor)}` : "";
    return request<DocumentPage>(`/review-queue?limit=25${query}`);
  },
  document: (digest: string) => request<RoutedDocument>(`/documents/${digest}`),
  upload: (image: Blob, localOnly: boolean) => {
    const form = new FormData();
    form.append("file", image, "receipt");
    if (localOnly) form.append("local_only", "true");
    return request<RoutedDocument>("/documents", { method: "POST", body: form });
  },
  /** Runs extraction again on a stored image, for a receipt that has no answer. */
  retry: async (digest: string) => {
    const stored = await fetch(imageUrl(digest));
    if (!stored.ok) throw new ApiError(stored.status, "image_unavailable", "Couldn't reload that image.");
    return api.upload(await stored.blob(), false);
  },
  correct: (digest: string, receipt: unknown, confirmMismatch: boolean) =>
    post<RoutedDocument>(`/documents/${digest}/correction`, {
      receipt,
      confirm_mismatch: confirmMismatch,
    }),
  dismiss: (digest: string, reason: string) =>
    post<RoutedDocument>(`/documents/${digest}/dismissal`, { reason }),
  demoSamples: () => request<DemoSample[]>("/demo/samples"),
  /** Runs the real validator on a visitor's edit. Nothing is saved. */
  demoCheck: (receipt: Receipt) =>
    post<{ passed: boolean; issues: string[] }>("/demo/check", { receipt }),
};

export function demoImageUrl(id: string): string {
  return `/api/demo/samples/${id}/image`;
}

export function imageUrl(digest: string): string {
  return `/api/documents/${digest}/image`;
}
