import { expect, type Page, test } from "@playwright/test";

import { apiError, jsonResponse, mockApi, routedDocument, WORST_CASE } from "./mock-api";

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

test.describe("leaderboard", () => {
  test("says what the router does and how far to trust the numbers", async ({ page }) => {
    await mockApi(page);
    await page.goto("/");
    await expect(page).toHaveURL(/\/leaderboard$/);
    await expect(page).toHaveTitle("Model leaderboard · Receipt IQ");
    await expect(page.getByRole("heading", { name: "Model leaderboard", level: 1 })).toBeVisible();
    await expect(page.getByText("The router tries GPT-5 mini first")).toBeVisible();
    await expect(page.getByText("Provisional.")).toBeVisible();
    await expect(page.getByText("73 receipts")).toBeVisible();
    await expect(page.getByText("Last resort: slower than 60 s")).toBeVisible();
    await expect(page.getByText("est.")).toBeVisible();
    await expectNoSidewaysScroll(page);
  });

  test("the chart's numbers are in the table too", async ({ page }) => {
    await mockApi(page);
    await page.goto("/leaderboard");
    await expect(page.getByRole("img", { name: /GPT-5 mini: 78\.1% at \$1\.11/ })).toBeVisible();
    const row = page.getByRole("row", { name: /GPT-5 mini/ });
    await expect(row).toContainText("78.1%");
    await expect(row).toContainText("$1.11");
  });

  test("the table sorts by the column you pick", async ({ page }) => {
    await mockApi(page);
    await page.goto("/leaderboard");
    const firstProvider = page.locator("tbody tr").first().getByRole("rowheader");
    await expect(firstProvider).toContainText("Gemini");
    await page.getByRole("button", { name: "Median time" }).click();
    await expect(firstProvider).toContainText("GPT-5 mini");
    await expect(page.getByRole("columnheader", { name: "Median time" })).toHaveAttribute(
      "aria-sort",
      "descending",
    );
  });

  test("shows a skeleton while loading", async ({ page }) => {
    await mockApi(page, { leaderboard: () => new Promise(() => {}) });
    await page.goto("/leaderboard");
    await expect(page.getByRole("status", { name: "Loading scores" })).toBeVisible();
  });

  test("an error names the problem, and retrying works", async ({ page }) => {
    let healthy = false;
    await mockApi(page, {
      leaderboard: (route) =>
        healthy
          ? route.fallback()
          : apiError(route, 429, "rate_limited", "Too many requests. Try again in 30 s."),
    });
    await page.route("**/api/leaderboard", async (route) => {
      if (healthy) {
        const { LEADERBOARD } = await import("./mock-api");
        return jsonResponse(route, LEADERBOARD);
      }
      return route.fallback();
    });
    await page.goto("/leaderboard");
    const alert = page.getByRole("alert");
    await expect(alert).toContainText("Couldn't load the scores");
    await expect(alert).toContainText("Too many requests. Try again in 30 s.");
    healthy = true;
    await page.getByRole("button", { name: "Try again" }).click();
    await expect(page.getByText("Provisional.")).toBeVisible();
  });
});

test.describe("routing log", () => {
  test("is behind the login, and a wrong password says so", async ({ page }) => {
    await mockApi(page);
    await page.goto("/routing-log");
    await expect(page.getByRole("heading", { name: "Log in to see" })).toBeVisible();
    await page.getByLabel("Owner password").fill("wrong");
    await page.getByRole("button", { name: "Log in" }).click();
    await expect(page.getByRole("alert")).toHaveText("That password isn't right.");
  });

  test("logging in shows the log, and logging out hides it", async ({ page }) => {
    await mockApi(page);
    await page.goto("/routing-log");
    await page.getByLabel("Owner password").fill("right-password");
    await page.keyboard.press("Enter");
    await expect(page.getByRole("button", { name: /Needs review/ })).toBeVisible();
    await page.getByRole("button", { name: "Log out" }).click();
    await expect(page.getByRole("heading", { name: "Log in to see" })).toBeVisible();
    await expect(page.getByRole("button", { name: /Needs review/ })).toHaveCount(0);
  });

  test("the login page goes back where you came from", async ({ page }) => {
    await mockApi(page);
    await page.goto("/leaderboard");
    await page.getByRole("link", { name: "Log in" }).click();
    await expect(page).toHaveURL(/\/login$/);
    await page.getByLabel("Owner password").fill("right-password");
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/\/leaderboard$/);
    await expect(page.getByRole("button", { name: "Log out" })).toBeVisible();
  });

  test("a row shows the route, and opens to show why", async ({ page }) => {
    await mockApi(page, { loggedIn: true });
    await page.goto("/routing-log");
    const row = page.getByRole("button", { name: /Needs review/ });
    await expect(row).toContainText("GPT flagged");
    await expect(row).toContainText("Gemini flagged");
    await expect(row).toHaveAttribute("aria-expanded", "false");
    await row.click();
    await expect(row).toHaveAttribute("aria-expanded", "true");
    await expect(page.getByText("total says 123000")).toBeVisible();
    await row.click();
    await expect(page.getByText("total says 123000")).toHaveCount(0);
  });

  test("every status has a word, not only a color", async ({ page }) => {
    await mockApi(page, { loggedIn: true });
    await page.goto("/routing-log");
    for (const word of ["Passed", "Needs review", "Dead letter", "Corrected", "Dismissed", "Failed", "Processing"]) {
      await expect(page.getByRole("button", { name: word }).first()).toBeVisible();
    }
  });

  test("long reasons stay inside the page when opened", async ({ page }, testInfo) => {
    await mockApi(page, { loggedIn: true });
    await page.goto("/routing-log");
    await page.getByRole("button", { name: /Dead letter/ }).click();
    await expect(page.getByText("gave up after 5 retries")).toBeVisible();
    await page.getByRole("button", { name: /Passed/ }).first().click();
    await expect(page.getByText("Only a provider missing the accuracy target")).toBeVisible();
    await expectNoSidewaysScroll(page);
    await screenshot(page, "routing-log-open", testInfo.project.name);
  });

  test("load more adds the next page", async ({ page }) => {
    await mockApi(page, {
      loggedIn: true,
      routingLog: (route) => {
        const cursor = new URL(route.request().url()).searchParams.get("cursor");
        return jsonResponse(
          route,
          cursor
            ? { items: [routedDocument(40)], next_cursor: null }
            : { items: [routedDocument(30)], next_cursor: "page 2" },
        );
      },
    });
    await page.goto("/routing-log");
    await expect(page.getByRole("button", { name: /Passed/ })).toHaveCount(1);
    await page.getByRole("button", { name: "Load more" }).click();
    await expect(page.getByRole("button", { name: /Passed/ })).toHaveCount(2);
    await expect(page.getByRole("button", { name: "Load more" })).toHaveCount(0);
  });

  test("the empty log says what would fill it", async ({ page }, testInfo) => {
    await mockApi(page, {
      loggedIn: true,
      routingLog: (route) => jsonResponse(route, { items: [], next_cursor: null }),
    });
    await page.goto("/routing-log");
    await expect(page.getByText("No receipts routed yet")).toBeVisible();
    await screenshot(page, "routing-log-empty", testInfo.project.name);
  });

  test("an error offers a retry", async ({ page }) => {
    await mockApi(page, {
      loggedIn: true,
      routingLog: (route) => apiError(route, 503, "unavailable", "Something's temporarily down."),
    });
    await page.goto("/routing-log");
    await expect(page.getByRole("alert")).toContainText("Something's temporarily down.");
    await expect(page.getByRole("button", { name: "Try again" })).toBeVisible();
  });
});

test.describe("app shell", () => {
  test("the links move between pages and mark the current one", async ({ page }) => {
    await mockApi(page);
    await page.goto("/leaderboard");
    const nav = page.getByRole("navigation", { name: "Main" });
    await expect(nav.getByRole("link", { name: "Leaderboard" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    await nav.getByRole("link", { name: "Routing log" }).click();
    await expect(page).toHaveURL(/\/routing-log$/);
    await expect(page).toHaveTitle("Routing log · Receipt IQ");
    await expect(nav.getByRole("link", { name: "Routing log" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    await expect(page.locator("main")).toBeFocused();
  });

  test("the first key press reaches a skip link", async ({ page }) => {
    await mockApi(page);
    await page.goto("/leaderboard");
    await page.keyboard.press("Tab");
    await expect(page.getByRole("link", { name: "Skip to content" })).toBeFocused();
  });

  test("the theme follows the system, and a choice is remembered", async ({ page }) => {
    await page.emulateMedia({ colorScheme: "dark" });
    await mockApi(page);
    await page.goto("/leaderboard");
    const background = () =>
      page.evaluate(() => getComputedStyle(document.body).backgroundColor);
    expect(await background()).toBe("rgb(16, 17, 20)");

    await page.getByRole("button", { name: "Switch to light theme" }).click();
    expect(await background()).toBe("rgb(255, 255, 255)");
    await page.reload();
    await expect(page.getByRole("button", { name: "Switch to dark theme" })).toBeVisible();
    expect(await background()).toBe("rgb(255, 255, 255)");
  });

  test("an unknown address gets a way back", async ({ page }) => {
    await mockApi(page);
    await page.goto("/no-such-page");
    await expect(page.getByRole("heading", { name: "That page doesn't exist" })).toBeVisible();
    await page.getByRole("link", { name: "Go to the leaderboard" }).click();
    await expect(page).toHaveURL(/\/leaderboard$/);
  });
});

test.describe("screenshots in both themes", () => {
  for (const colorScheme of ["light", "dark"] as const) {
    test(`leaderboard and routing log, ${colorScheme}`, async ({ page }, testInfo) => {
      await page.emulateMedia({ colorScheme });
      await mockApi(page, { loggedIn: true });
      await page.goto("/leaderboard");
      await expect(page.getByRole("heading", { name: "Scores" })).toBeVisible();
      await expectNoSidewaysScroll(page);
      await screenshot(page, `leaderboard-${colorScheme}`, testInfo.project.name);

      await page.goto("/routing-log");
      await expect(page.getByRole("button", { name: /Dead letter/ })).toBeVisible();
      expect(WORST_CASE.length).toBeGreaterThan(1);
      await expectNoSidewaysScroll(page);
      await screenshot(page, `routing-log-${colorScheme}`, testInfo.project.name);
    });
  }
});
