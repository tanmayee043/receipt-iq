import type { Page, Route } from "@playwright/test";

const provider = (
  name: string,
  fully: number,
  f1: number,
  cost: number,
  median: number,
  catchRate: number,
  meetsPolicy: boolean,
  costNote: string,
  local = false,
) => ({
  provider: name,
  local,
  fully_correct: fully,
  total_correct: fully,
  item_f1: f1,
  catch_rate: catchRate,
  false_alarm_rate: 0,
  failure_rate: 0,
  median_seconds: median,
  cost_per_1000: cost,
  cost_note: costNote,
  meets_policy: meetsPolicy,
});

export const LEADERBOARD = {
  split: "validation",
  receipts: 73,
  accuracy_target: 0.75,
  max_seconds: 60,
  routing_order: ["gpt", "gemini", "gemma", "azure", "local"],
  providers: [
    provider("local", 0.041, 0.289, 0, 23.1, 0.457, false, "runs on this machine", true),
    provider("gemini", 0.863, 0.946, 5.4, 11.6, 0.3, true, "estimate: assuming GPT's tokens"),
    provider("gemma", 0.795, 0.917, 0, 111, 0.6, false, "free tier only"),
    provider("azure", 0.658, 0.893, 10, 9, 0.8, false, "$10 per 1,000 pages"),
    provider("gpt", 0.781, 0.878, 1.11, 8.3, 0.438, true, "measured tokens"),
  ],
};

export function routedDocument(index: number, overrides: Record<string, unknown> = {}) {
  return {
    digest: String(index).padStart(2, "0").repeat(32),
    status: "passed",
    provider: "gpt",
    receipt: null,
    corrected_receipt: null,
    confirmed_mismatch: false,
    issues: [],
    attempts: [{ provider: "gpt", outcome: "passed", reason: "", seconds: 8.3 }],
    met_target: true,
    seconds: 8.3,
    failure_kind: null,
    failure_reason: null,
    created_at: "2026-11-03T14:05:00Z",
    updated_at: "2026-11-03T14:05:00Z",
    corrected_at: null,
    ...overrides,
  };
}

export const FLAGGED = routedDocument(1, {
  status: "needs_review",
  provider: "gemini",
  seconds: 20.1,
  issues: [
    {
      code: "total_mismatch",
      message: "subtotal - discount + service + tax + tip = 83000, total says 123000",
      expected: "83000",
      actual: "123000",
      location: "total",
    },
  ],
  attempts: [
    { provider: "gpt", outcome: "needs_review", reason: "", seconds: 8.3 },
    { provider: "gemini", outcome: "needs_review", reason: "", seconds: 11.8 },
  ],
});

/** Every kind of row, with the longest text each can hold. */
export const WORST_CASE = [
  FLAGGED,
  routedDocument(2, {
    status: "dead_letter",
    provider: "auto",
    seconds: null,
    met_target: null,
    attempts: [
      { provider: "gpt", outcome: "unavailable", reason: "HTTP 429 ".repeat(30), seconds: 0.2 },
      { provider: "gemini", outcome: "skipped", reason: "circuit breaker open", seconds: 0 },
      { provider: "gemma", outcome: "failed", reason: "invalid output", seconds: 110 },
      { provider: "azure", outcome: "unavailable", reason: "x".repeat(300), seconds: 1 },
      { provider: "local", outcome: "failed", reason: "unreadable", seconds: 23 },
    ],
    failure_kind: "retries_exhausted",
    failure_reason: `gave up after 5 retries: ${"y".repeat(300)}`,
  }),
  routedDocument(3, {
    status: "passed",
    provider: "gemma",
    met_target: false,
    attempts: [
      { provider: "gpt", outcome: "unavailable", reason: "401", seconds: 0.1 },
      { provider: "gemma", outcome: "passed", reason: "", seconds: 111.4 },
    ],
  }),
  routedDocument(4, { status: "corrected" }),
  routedDocument(5, { status: "dismissed" }),
  routedDocument(6, { status: "failed" }),
  routedDocument(7, { status: "processing", attempts: [{ provider: "new-model", outcome: "something_new", reason: "", seconds: 1 }] }),
];

interface Mock {
  loggedIn?: boolean;
  leaderboard?: (route: Route) => Promise<void>;
  routingLog?: (route: Route) => Promise<void>;
}

const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

export const apiError = (route: Route, status: number, code: string, message: string) =>
  json(route, { error: { code, message } }, status);

/** Stands in for the API. The login succeeds only with the password "right-password". */
export async function mockApi(page: Page, mock: Mock = {}) {
  let loggedIn = mock.loggedIn ?? false;
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/session") return json(route, { logged_in: loggedIn });
    if (path === "/api/login") {
      const { password } = request.postDataJSON() as { password: string };
      if (password !== "right-password") {
        return apiError(route, 401, "wrong_password", "That password isn't right.");
      }
      loggedIn = true;
      return json(route, { logged_in: true });
    }
    if (path === "/api/logout") {
      loggedIn = false;
      return json(route, { logged_in: false });
    }
    if (path === "/api/leaderboard") {
      return mock.leaderboard ? mock.leaderboard(route) : json(route, LEADERBOARD);
    }
    if (path === "/api/routing-log") {
      if (!loggedIn) return apiError(route, 401, "not_logged_in", "Log in first.");
      if (mock.routingLog) return mock.routingLog(route);
      return json(route, { items: WORST_CASE, next_cursor: null });
    }
    return apiError(route, 404, "http_404", "Not Found");
  });
}

export { json as jsonResponse };
