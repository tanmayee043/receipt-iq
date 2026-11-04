import { expect, type Page, test } from "@playwright/test";

import { apiError, jsonResponse, mockApi, mockDocuments, PASSED_RECEIPT } from "./mock-api";

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

/** The cell showing what this person pays. */
const pays = (page: Page, person: string) =>
  page.getByRole("row", { name: new RegExp(`^${person} `) }).getByRole("cell").last();

const had = (page: Page, item: string) => page.getByRole("group", { name: `Who had ${item}` });

test.describe("demo for visitors", () => {
  test("is the front door, and needs no login", async ({ page }, testInfo) => {
    await mockApi(page);
    await page.goto("/");
    await expect(page).toHaveURL(/\/demo$/);
    await expect(page.getByRole("heading", { name: /Turn a receipt/ })).toBeVisible();
    await expect(page.getByText("no provider is called")).toBeVisible();
    await expect(page.getByRole("link", { name: "CC BY 4.0" })).toBeVisible();
    await expectNoSidewaysScroll(page);
    await screenshot(page, "demo", testInfo.project.name);
  });

  test("visitors only see links they can use", async ({ page }) => {
    await mockApi(page);
    await page.goto("/demo");
    const nav = page.getByRole("navigation", { name: "Main" });
    await expect(nav.getByRole("link", { name: "Leaderboard" })).toBeVisible();
    await expect(nav.getByRole("link", { name: "Upload" })).toHaveCount(0);
    await expect(nav.getByRole("link", { name: "Review" })).toHaveCount(0);
    await expect(nav.getByRole("link", { name: "Routing log" })).toHaveCount(0);
  });

  test("opening a sample uploads nothing and shows the route", async ({ page }) => {
    let posts = 0;
    await mockApi(page);
    page.on("request", (request) => {
      if (request.method() === "POST") posts += 1;
    });
    await page.goto("/demo");
    await page.getByRole("link", { name: /An easy one/ }).click();
    await expect(page).toHaveURL(/\/demo\/sample-1$/);
    await expect(page.getByText("Answered by GPT-5 mini")).toBeVisible();
    await expect(page.getByText("FRIED RICE").first()).toBeVisible();
    expect(posts).toBe(0);
  });

  test("a flagged sample is fixed first, using the real check, then split", async ({ page }) => {
    await mockApi(page);
    await page.goto("/demo/sample-2");
    await expect(page.getByText("total says 53000")).toBeVisible();
    await expect(page.getByRole("heading", { name: "Split the bill" })).toHaveCount(0);

    const total = page.getByLabel("Total", { exact: true });
    await total.fill("99000");
    await page.getByRole("button", { name: "Save correction" }).click();
    await expect(page.getByRole("alert")).toContainText("total says 99000");

    await total.fill("35000");
    await page.getByRole("button", { name: "Save correction" }).click();
    await expect(page.getByRole("heading", { name: "Your fix" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Split the bill" })).toBeVisible();
    await expect(page.getByText("Corrected", { exact: true })).toBeVisible();
  });

  test("a demo fix is gone after a reload, because nothing was saved", async ({ page }) => {
    await mockApi(page);
    await page.goto("/demo/sample-2");
    await page.getByLabel("Total", { exact: true }).fill("35000");
    await page.getByRole("button", { name: "Save correction" }).click();
    await expect(page.getByRole("heading", { name: "Your fix" })).toBeVisible();
    await page.reload();
    await expect(page.getByLabel("Total", { exact: true })).toHaveValue("53000");
  });

  test("errors and missing samples say so", async ({ page }) => {
    await mockApi(page, {
      demoSamples: (route) => apiError(route, 429, "rate_limited", "Too many requests."),
    });
    await page.goto("/demo");
    await expect(page.getByRole("alert")).toContainText("Too many requests.");
  });

  test("an unknown sample offers a way back", async ({ page }) => {
    await mockApi(page);
    await page.goto("/demo/sample-99");
    await expect(page.getByText("There's no sample with that name")).toBeVisible();
    await page.getByRole("link", { name: "See all the samples" }).click();
    await expect(page).toHaveURL(/\/demo$/);
  });

  test("no samples built says what to do", async ({ page }) => {
    await mockApi(page, { demoSamples: (route) => jsonResponse(route, []) });
    await page.goto("/demo");
    await expect(page.getByText("No samples are available")).toBeVisible();
  });
});

test.describe("splitting a bill", () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page);
    await page.goto("/demo/sample-1");
    await page.evaluate(() => localStorage.clear());
    await page.reload();
    await expect(page.getByRole("heading", { name: "Split the bill" })).toBeVisible();
  });

  test("starts with everything unassigned, and still adds up", async ({ page }, testInfo) => {
    await expect(pays(page, "Not assigned yet")).toHaveText("35,000");
    await expect(pays(page, "Person 1")).toHaveText("0");
    await expect(page.getByRole("row", { name: /Receipt total/ })).toContainText("35,000");
    await expectNoSidewaysScroll(page);
    await screenshot(page, "split-empty", testInfo.project.name);
  });

  test("assigning items moves them to that person", async ({ page }, testInfo) => {
    await had(page, "ICED TEA").getByRole("button", { name: "Person 1" }).click();
    await had(page, "FRIED RICE").getByRole("button", { name: "Person 2" }).click();
    await expect(pays(page, "Person 1")).toHaveText("10,000");
    await expect(pays(page, "Person 2")).toHaveText("25,000");
    await expect(page.getByRole("row", { name: /Not assigned yet/ })).toHaveCount(0);
    await expect(had(page, "ICED TEA").getByRole("button", { name: "Person 1" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await expectNoSidewaysScroll(page);
    await screenshot(page, "split-done", testInfo.project.name);
  });

  test("everyone shares an item, and it can be undone", async ({ page }) => {
    await page.getByRole("button", { name: "Add a person" }).click();
    const tea = had(page, "ICED TEA");
    await tea.getByRole("button", { name: "Everyone" }).click();
    await had(page, "FRIED RICE").getByRole("button", { name: "Person 1" }).click();
    // 10,000 between three is 3,334 + 3,333 + 3,333: no unit is lost.
    await expect(pays(page, "Person 1")).toHaveText("28,334");
    await expect(pays(page, "Person 2")).toHaveText("3,333");
    await expect(pays(page, "Person 3")).toHaveText("3,333");
    await tea.getByRole("button", { name: "Everyone" }).click();
    await expect(tea.getByRole("button", { name: "Person 1" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });

  test("people can be renamed and removed", async ({ page }) => {
    await page.getByLabel("Name of Person 1").fill("Ana");
    const tea = had(page, "ICED TEA");
    await tea.getByRole("button", { name: "Ana" }).click();
    await expect(pays(page, "Ana")).toHaveText("10,000");
    await page.getByRole("button", { name: "Remove Ana" }).click();
    await expect(tea.getByRole("button", { name: "Ana" })).toHaveCount(0);
    await expect(pays(page, "Not assigned yet")).toHaveText("35,000");
    await expect(page.getByRole("button", { name: /^Remove/ })).toHaveCount(0);
  });

  test("the split survives a reload, in this browser", async ({ page }) => {
    await page.getByLabel("Name of Person 1").fill("Ana");
    await had(page, "ICED TEA").getByRole("button", { name: "Ana" }).click();
    await page.reload();
    await expect(pays(page, "Ana")).toHaveText("10,000");
  });

  test("keyboard works, and long names stay inside the page", async ({ page }, testInfo) => {
    await page.getByLabel("Name of Person 2").fill("W".repeat(40));
    const button = had(page, "ICED TEA").getByRole("button", { name: "Person 1" });
    await button.focus();
    await page.keyboard.press("Enter");
    await expect(button).toHaveAttribute("aria-pressed", "true");
    await expectNoSidewaysScroll(page);
    await screenshot(page, "split-long", testInfo.project.name);
  });
});

test("a real receipt can be split once it has an answer", async ({ page }) => {
  await mockApi(page, { loggedIn: true });
  await mockDocuments(page, [PASSED_RECEIPT]);
  await page.goto(`/receipts/${PASSED_RECEIPT.digest}`);
  await had(page, "ICED TEA").getByRole("button", { name: "Person 1" }).click();
  await expect(pays(page, "Person 1")).toHaveText("10,000");
});
