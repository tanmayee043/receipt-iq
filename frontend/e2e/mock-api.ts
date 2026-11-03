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

export const PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==",
  "base64",
);

export const RECEIPT = {
  vendor: "Warung Makan",
  currency: "IDR",
  items: [
    { name: "ICED TEA", quantity: "2", unit_price: "5000", total_price: "10000", discount: null },
    { name: "FRIED RICE", quantity: "1", unit_price: null, total_price: "25000", discount: null },
  ],
  subtotal: "35000",
  discount: null,
  service_charge: null,
  tax: null,
  tip: null,
  total: "53000",
};

const totalIssue = {
  code: "total_mismatch",
  message: "subtotal - discount + service + tax + tip = 35000, total says 53000",
  expected: "35000",
  actual: "53000",
  location: "total",
};
const lineIssue = {
  code: "line_arithmetic",
  message: "'ICED TEA': 2 x 5000 should be 10000, receipt says 11000",
  expected: "10000",
  actual: "11000",
  location: "items[0]",
};

export const FLAGGED_RECEIPT = routedDocument(11, {
  status: "needs_review",
  receipt: RECEIPT,
  issues: [totalIssue, lineIssue],
});
export const PASSED_RECEIPT = routedDocument(12, {
  receipt: { ...RECEIPT, total: "35000" },
});
export const DEAD_RECEIPT = routedDocument(13, {
  status: "dead_letter",
  provider: "auto",
  attempts: [],
  seconds: null,
  failure_kind: "retries_exhausted",
  failure_reason: "gave up after 5 retries: every provider was busy",
});
export const FAILED_RECEIPT = routedDocument(14, {
  status: "failed",
  provider: "auto",
  attempts: [],
  seconds: null,
  failure_kind: "task_failed",
  failure_reason: "RuntimeError: set the provider's key",
});

/**
 * A small stand-in for the document endpoints, keeping state across requests. A correction
 * with the total still wrong is refused until confirmed, as on the real server.
 */
export async function mockDocuments(page: Page, documents: Record<string, unknown>[]) {
  const store = new Map(documents.map((document) => [document.digest as string, { ...document }]));
  const uploads: { localOnly: boolean }[] = [];
  const save = (digest: string, changes: Record<string, unknown>) => {
    const updated = {
      ...store.get(digest),
      ...changes,
      updated_at: new Date().toISOString(),
    };
    store.set(digest, updated);
    return updated;
  };

  await page.route(/\/api\/documents(\/|$)/, async (route) => {
    const request = route.request();
    const parts = new URL(request.url()).pathname.split("/").slice(3);
    const digest = parts[0];

    if (!digest) {
      const body = request.postDataBuffer()?.toString("latin1") ?? "";
      uploads.push({ localOnly: body.includes('name="local_only"') });
      return json(route, { ...(documents[0] as object) }, 202);
    }
    const document = store.get(digest);
    if (!document) return apiError(route, 404, "not_found", "There's no receipt with that hash.");
    if (parts[1] === "image") {
      return route.fulfill({ status: 200, contentType: "image/png", body: PNG });
    }
    if (parts[1] === "correction") {
      const body = request.postDataJSON() as {
        receipt: { total: string };
        confirm_mismatch: boolean;
      };
      const addsUp = body.receipt.total === "35000";
      if (!addsUp && !body.confirm_mismatch) {
        return json(
          route,
          {
            error: {
              code: "correction_rejected",
              message: "That correction doesn't add up. Confirm it if the receipt itself is misprinted.",
              details: { issues: [`total says ${body.receipt.total}`] },
            },
          },
          422,
        );
      }
      return json(
        route,
        save(digest, {
          status: "corrected",
          corrected_receipt: body.receipt,
          confirmed_mismatch: !addsUp,
        }),
      );
    }
    if (parts[1] === "dismissal") {
      const { reason } = request.postDataJSON() as { reason: string };
      return json(route, save(digest, { status: "dismissed", failure_reason: reason }));
    }
    return json(route, document);
  });
  return { uploads, store, save };
}

interface Mock {
  loggedIn?: boolean;
  reviewQueue?: (route: Route) => Promise<void>;
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
    if (path === "/api/review-queue") {
      if (!loggedIn) return apiError(route, 401, "not_logged_in", "Log in first.");
      if (mock.reviewQueue) return mock.reviewQueue(route);
      return json(route, { items: [], next_cursor: null });
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
