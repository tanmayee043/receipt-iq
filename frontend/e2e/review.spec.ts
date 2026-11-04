import { expect, type Page, test } from "@playwright/test";

import {
  apiError,
  DEAD_RECEIPT,
  FAILED_RECEIPT,
  FLAGGED_RECEIPT,
  jsonResponse,
  mockApi,
  mockDocuments,
  PASSED_RECEIPT,
  PNG,
  RECEIPT,
  routedDocument,
} from "./mock-api";

const FILE = { name: "receipt.png", mimeType: "image/png", buffer: PNG };
const url = (document: { digest: string }) => `/receipts/${document.digest}`;

async function expectNoSidewaysScroll(page: Page) {
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);
}

async function screenshot(page: Page, name: string, projectName: string) {
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ path: `screenshots/${projectName}-${name}.png`, fullPage: true });
}

test.describe("upload", () => {
  test("needs the login", async ({ page }) => {
    await mockApi(page);
    await page.goto("/upload");
    await expect(page.getByRole("heading", { name: "Log in to upload" })).toBeVisible();
    await expect(page.locator('input[type="file"]')).toHaveCount(0);
  });

  test("an upload opens the receipt, which updates by itself", async ({ page }, testInfo) => {
    const processing = routedDocument(20, {
      status: "processing",
      provider: null,
      attempts: [],
      seconds: null,
    });
    await mockApi(page, { loggedIn: true });
    const documents = await mockDocuments(page, [processing]);
    await page.goto("/upload");
    await screenshot(page, "upload", testInfo.project.name);
    await page.locator('input[type="file"]').setInputFiles(FILE);

    await expect(page).toHaveURL(new RegExp(url(processing)));
    await expect(page.getByText("Reading this receipt")).toBeVisible();
    documents.save(processing.digest, {
      status: "passed",
      provider: "gpt",
      receipt: PASSED_RECEIPT.receipt,
      attempts: PASSED_RECEIPT.attempts,
      seconds: 8.3,
    });
    await expect(page.getByText("FRIED RICE").first()).toBeVisible({ timeout: 6000 });
    await expect(page.getByText("Answered by GPT-5 mini")).toBeVisible();
    expect(documents.uploads[0]?.localOnly).toBe(false);
  });

  test("keep on this machine is sent with the upload", async ({ page }) => {
    await mockApi(page, { loggedIn: true });
    const documents = await mockDocuments(page, [PASSED_RECEIPT]);
    await page.goto("/upload");
    await page.getByRole("checkbox", { name: /Keep this on this machine/ }).check();
    await page.locator('input[type="file"]').setInputFiles(FILE);
    await expect(page).toHaveURL(new RegExp(url(PASSED_RECEIPT)));
    expect(documents.uploads[0]?.localOnly).toBe(true);
  });

  test("the wrong file type is refused before uploading", async ({ page }) => {
    await mockApi(page, { loggedIn: true });
    const documents = await mockDocuments(page, [PASSED_RECEIPT]);
    await page.goto("/upload");
    await page.locator('input[type="file"]').setInputFiles({
      name: "notes.pdf",
      mimeType: "application/pdf",
      buffer: Buffer.from("%PDF"),
    });
    await expect(page.getByRole("alert")).toHaveText("That isn't a JPEG or PNG image.");
    expect(documents.uploads).toHaveLength(0);
  });

  test("a refusal from the server is shown in its own words", async ({ page }) => {
    await mockApi(page, { loggedIn: true });
    await page.route(/\/api\/documents$/, (route) =>
      apiError(route, 429, "rate_limited", "Too many requests. Try again in 40 s."),
    );
    await page.goto("/upload");
    await page.locator('input[type="file"]').setInputFiles(FILE);
    await expect(page.getByRole("alert")).toHaveText("Too many requests. Try again in 40 s.");
  });
});

test.describe("review queue", () => {
  test("says what would fill it when empty", async ({ page }) => {
    await mockApi(page, { loggedIn: true });
    await page.goto("/review");
    await expect(page.getByText("Nothing is waiting for review")).toBeVisible();
  });

  test("shows each receipt's reason and opens it", async ({ page }, testInfo) => {
    await mockApi(page, {
      loggedIn: true,
      reviewQueue: (route) =>
        jsonResponse(route, { items: [FLAGGED_RECEIPT, DEAD_RECEIPT], next_cursor: null }),
    });
    await mockDocuments(page, [FLAGGED_RECEIPT, DEAD_RECEIPT]);
    await page.goto("/review");
    await expect(page.getByText("total says 53000 (and 1 more)")).toBeVisible();
    await expect(page.getByText("every provider was busy")).toBeVisible();
    await expectNoSidewaysScroll(page);
    await screenshot(page, "review-queue", testInfo.project.name);
    await page.getByRole("link", { name: /Needs review/ }).click();
    await expect(page).toHaveURL(new RegExp(url(FLAGGED_RECEIPT)));
  });

  test("load more adds the next page, and errors offer a retry", async ({ page }) => {
    let fail = true;
    await mockApi(page, {
      loggedIn: true,
      reviewQueue: (route) => {
        if (fail) return apiError(route, 503, "unavailable", "Something's temporarily down.");
        const cursor = new URL(route.request().url()).searchParams.get("cursor");
        return jsonResponse(
          route,
          cursor
            ? { items: [DEAD_RECEIPT], next_cursor: null }
            : { items: [FLAGGED_RECEIPT], next_cursor: "next" },
        );
      },
    });
    await mockDocuments(page, [FLAGGED_RECEIPT, DEAD_RECEIPT]);
    await page.goto("/review");
    await expect(page.getByRole("alert")).toContainText("Something's temporarily down.", {
      timeout: 15000,
    });
    fail = false;
    await page.getByRole("button", { name: "Try again" }).click();
    await expect(page.getByRole("link", { name: /Needs review/ })).toBeVisible();
    await page.getByRole("button", { name: "Load more" }).click();
    await expect(page.getByRole("link", { name: /Dead letter/ })).toBeVisible();
  });
});

test.describe("correcting a receipt", () => {
  test("shows the image beside the answer, with the problem where it is", async ({
    page,
  }, testInfo) => {
    await mockApi(page, { loggedIn: true });
    await mockDocuments(page, [FLAGGED_RECEIPT]);
    await page.goto(url(FLAGGED_RECEIPT));
    await expect(page.getByRole("img", { name: "The uploaded receipt" })).toBeVisible();
    await expect(page.getByLabel("Total", { exact: true })).toHaveValue("53000");
    await expect(page.getByLabel("Total", { exact: true })).toHaveAttribute("aria-invalid", "true");
    await expect(page.getByText("total says 53000")).toBeVisible();
    await expect(page.getByText("2 x 5000 should be 10000")).toBeVisible();
    await expect(page.getByLabel("Line 1 name")).toHaveValue("ICED TEA");
    await expectNoSidewaysScroll(page);
    await screenshot(page, "receipt-review", testInfo.project.name);
  });

  test("a fix that adds up is saved, and the model's answer stays", async ({ page }) => {
    await mockApi(page, { loggedIn: true });
    await mockDocuments(page, [FLAGGED_RECEIPT]);
    await page.goto(url(FLAGGED_RECEIPT));
    await page.getByLabel("Total", { exact: true }).fill("35000");
    await page.getByRole("button", { name: "Save correction" }).click();

    await expect(page.getByRole("heading", { name: "Corrected answer" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Save correction" })).toHaveCount(0);
    await page.getByText("The model's answer").click();
    await expect(page.getByText("53,000")).toBeVisible();
    await expect(page.getByRole("link", { name: "Back to the review queue" })).toBeVisible();
  });

  test("a fix that doesn't add up needs confirming", async ({ page }) => {
    await mockApi(page, { loggedIn: true });
    await mockDocuments(page, [FLAGGED_RECEIPT]);
    await page.goto(url(FLAGGED_RECEIPT));
    await page.getByLabel("Total", { exact: true }).fill("99000");
    await page.getByRole("button", { name: "Save correction" }).click();

    const alert = page.getByRole("alert");
    await expect(alert).toContainText("doesn't add up");
    await expect(alert).toContainText("total says 99000");
    await page.getByRole("button", { name: "Save anyway, the receipt is misprinted" }).click();
    await expect(page.getByText("Saved as confirmed")).toBeVisible();
  });

  test("typing nonsense is caught before sending", async ({ page }) => {
    await mockApi(page, { loggedIn: true });
    await mockDocuments(page, [FLAGGED_RECEIPT]);
    await page.goto(url(FLAGGED_RECEIPT));
    await page.getByLabel("Total", { exact: true }).fill("lots");
    await page.getByRole("button", { name: "Save correction" }).click();
    await expect(page.getByRole("alert")).toContainText("The total is required");
  });

  test("lines can be added and removed", async ({ page }) => {
    await mockApi(page, { loggedIn: true });
    await mockDocuments(page, [FLAGGED_RECEIPT]);
    await page.goto(url(FLAGGED_RECEIPT));
    await page.getByRole("button", { name: "Add a line" }).click();
    await expect(page.getByLabel("Line 3 name")).toHaveValue("");
    await page.getByRole("button", { name: "Save correction" }).click();
    await expect(page.getByRole("alert")).toHaveText("Line 3 needs a name.");
    await page.getByRole("button", { name: "Remove line 3" }).click();
    await expect(page.getByLabel("Line 3 name")).toHaveCount(0);
    await page.getByRole("button", { name: "Remove line 1" }).click();
    await expect(page.getByLabel("Line 1 name")).toHaveValue("FRIED RICE");
    await page.getByRole("button", { name: "Remove line 1" }).waitFor({ state: "detached" });
  });

  test("a dead letter starts empty and can be filled in by hand", async ({ page }, testInfo) => {
    await mockApi(page, { loggedIn: true });
    await mockDocuments(page, [DEAD_RECEIPT]);
    await page.goto(url(DEAD_RECEIPT));
    await expect(page.getByText("every provider was busy")).toBeVisible();
    await expect(page.getByLabel("Line 1 name")).toHaveValue("");
    await expectNoSidewaysScroll(page);
    await screenshot(page, "receipt-dead-letter", testInfo.project.name);
    await page.getByLabel("Line 1 name").fill("RICE");
    await page.getByLabel("Line total").fill("35000");
    await page.getByLabel("Total", { exact: true }).fill("35000");
    await page.getByRole("button", { name: "Save correction" }).click();
    await expect(page.getByRole("heading", { name: "Corrected answer" })).toBeVisible();
  });

  test("a receipt can be dismissed, with a reason", async ({ page }) => {
    await mockApi(page, { loggedIn: true });
    await mockDocuments(page, [DEAD_RECEIPT]);
    await page.goto(url(DEAD_RECEIPT));
    await page.getByRole("button", { name: "Dismiss this receipt" }).click();
    const dismiss = page.getByRole("button", { name: "Dismiss", exact: true });
    await expect(dismiss).toBeDisabled();
    await page.getByLabel("Why is there nothing to extract?").fill("It's a photo of a cat");
    await dismiss.click();
    await expect(page.getByText("It's a photo of a cat")).toBeVisible();
    await expect(page.getByRole("button", { name: "Save correction" })).toHaveCount(0);
  });

  test("a passed receipt is read-only until you choose to correct it", async ({ page }) => {
    await mockApi(page, { loggedIn: true });
    await mockDocuments(page, [PASSED_RECEIPT]);
    await page.goto(url(PASSED_RECEIPT));
    await expect(page.getByText("isn't proof")).toBeVisible();
    await expect(page.getByLabel("Total", { exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: "Correct this" }).click();
    await expect(page.getByLabel("Total", { exact: true })).toHaveValue("35000");
  });

  test("a failed run explains why and can be retried", async ({ page }) => {
    await mockApi(page, { loggedIn: true });
    const documents = await mockDocuments(page, [PASSED_RECEIPT, FAILED_RECEIPT]);
    await page.goto(url(FAILED_RECEIPT));
    await expect(page.getByText("set the provider's key")).toBeVisible();
    await expect(page.getByLabel("Total", { exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: "Try extraction again" }).click();
    await expect.poll(() => documents.uploads.length).toBe(1);
  });

  test("zoom toggles, and it's keyboard reachable", async ({ page }) => {
    await mockApi(page, { loggedIn: true });
    await mockDocuments(page, [PASSED_RECEIPT]);
    await page.goto(url(PASSED_RECEIPT));
    const zoom = page.getByRole("button", { name: "Zoom in" });
    await zoom.focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("button", { name: "Zoom out" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  test("an unknown receipt says so", async ({ page }) => {
    await mockApi(page, { loggedIn: true });
    await mockDocuments(page, []);
    await page.goto(`/receipts/${"f".repeat(64)}`);
    await expect(page.getByRole("alert")).toContainText("There's no receipt here");
  });

  test("long names and amounts stay inside the page", async ({ page }, testInfo) => {
    const long = routedDocument(21, {
      status: "needs_review",
      receipt: {
        ...RECEIPT,
        vendor: "V".repeat(200),
        items: [
          {
            name: "N".repeat(300),
            quantity: "1000000",
            unit_price: "9".repeat(30),
            total_price: "9".repeat(30),
            discount: "1".repeat(30),
          },
        ],
        total: "9".repeat(40),
      },
      issues: [
        {
          code: "total_mismatch",
          message: "x".repeat(400),
          expected: "1",
          actual: "2",
          location: "total",
        },
      ],
    });
    await mockApi(page, { loggedIn: true });
    await mockDocuments(page, [long]);
    await page.goto(url(long));
    await expect(page.getByLabel("Total", { exact: true })).toBeVisible();
    await expectNoSidewaysScroll(page);
    await screenshot(page, "receipt-long", testInfo.project.name);
  });
});
