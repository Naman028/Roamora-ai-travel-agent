import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

async function openWorkspace(page: Page) {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { level: 1, name: "Roamora AI" }),
  ).toBeVisible();
}

test("desktop presents three ordered panels", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await openWorkspace(page);

  const current = page.getByTestId("current-trip-panel");
  const chat = page.getByTestId("chat-panel");
  const saved = page.getByTestId("saved-trips-panel");
  await expect(current).toBeVisible();
  await expect(chat).toBeVisible();
  await expect(saved).toBeVisible();

  const [currentBox, chatBox, savedBox] = await Promise.all([
    current.boundingBox(),
    chat.boundingBox(),
    saved.boundingBox(),
  ]);
  expect(currentBox?.x).toBeLessThan(chatBox?.x ?? 0);
  expect(chatBox?.x).toBeLessThan(savedBox?.x ?? 0);
  await page.screenshot({
    path: "artifacts/screenshots/roamora-desktop.png",
    fullPage: true,
  });
});

test("tablet keeps chat primary and opens saved trips in a drawer", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1024, height: 820 });
  await openWorkspace(page);

  await expect(page.getByTestId("current-trip-panel")).toBeVisible();
  await expect(page.getByTestId("chat-panel")).toBeVisible();
  await expect(page.getByTestId("saved-trips-panel")).toBeHidden();

  const trigger = page.getByRole("button", { name: "Open saved trips" });
  await trigger.click();
  await expect(page.getByRole("dialog", { name: "Saved trips" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toBeHidden();
  await expect(trigger).toBeFocused();
  await page.screenshot({
    path: "artifacts/screenshots/roamora-tablet.png",
    fullPage: true,
  });
});

test("mobile navigation changes the single visible panel", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openWorkspace(page);

  const navigation = page.getByRole("navigation", {
    name: "Workspace panels",
  });
  await expect(navigation.getByRole("button", { name: "Chat" })).toHaveAttribute(
    "aria-current",
    "page",
  );
  await expect(page.getByTestId("chat-panel")).toBeVisible();
  await expect(page.getByTestId("current-trip-panel")).toBeHidden();

  await navigation.getByRole("button", { name: "Current trip" }).click();
  await expect(page.getByTestId("current-trip-panel")).toBeVisible();
  await expect(page.getByTestId("chat-panel")).toBeHidden();

  await navigation.getByRole("button", { name: "Saved trips" }).click();
  await expect(page.getByTestId("saved-trips-panel")).toBeVisible();

  await navigation.getByRole("button", { name: "Chat" }).click();
  await page.screenshot({
    path: "artifacts/screenshots/roamora-mobile.png",
    fullPage: true,
  });
});

test("composer preserves Enter and Shift+Enter behavior", async ({ page }) => {
  await page.route("**/api/chat", async (route) => {
    const requestId = "e2e-keyboard-request";
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      headers: { "X-Request-ID": requestId },
      body: JSON.stringify({
        session_id: "demo-user-1",
        selected_model: "fine_tuned",
        adapter_loaded: true,
        parse_success: true,
        fallback_used: false,
        retry_used: false,
        assistant_message: "A food-focused city break is ready.",
        dashboard_payload: {
          schema_version: "travel_dashboard_v1",
          intent: "itinerary_generation",
        },
      }),
    });
  });
  await openWorkspace(page);
  const composer = page.getByRole("textbox", {
    name: "Ask Roamora about your travel idea",
  });
  await composer.fill("A quiet city break");
  await composer.press("Shift+Enter");
  await composer.type("with good food");
  await expect(composer).toHaveValue("A quiet city break\nwith good food");
  await composer.press("Enter");
  await expect(
    page.getByText("A food-focused city break is ready."),
  ).toBeVisible();
});

for (const viewport of [
  { name: "desktop", width: 1440, height: 900 },
  { name: "tablet", width: 1024, height: 820 },
  { name: "mobile", width: 390, height: 844 },
]) {
  test(`${viewport.name} has no horizontal overflow`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await openWorkspace(page);
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - window.innerWidth,
    );
    expect(overflow).toBeLessThanOrEqual(0);
  });
}

test("primary workspace has no automatically detectable accessibility violations", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await openWorkspace(page);
  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations).toEqual([]);
});

test("unknown routes render an accessible not-found page", async ({ page }) => {
  await page.goto("/route-that-does-not-exist");
  await expect(
    page.getByRole("heading", { level: 1, name: "This route doesn’t exist" }),
  ).toBeVisible();
  await expect(page.getByRole("link", { name: "Back to Roamora" })).toBeVisible();
});

test("production responses include the security-header foundation", async ({
  page,
}) => {
  const response = await page.goto("/");
  expect(response).not.toBeNull();
  const headers = response?.headers() ?? {};

  expect(headers["x-content-type-options"]).toBe("nosniff");
  expect(headers["x-frame-options"]).toBe("DENY");
  expect(headers["referrer-policy"]).toBe("strict-origin-when-cross-origin");
  expect(headers["permissions-policy"]).toContain("geolocation=()");
  expect(headers["strict-transport-security"]).toContain("max-age=31536000");
  expect(headers["content-security-policy-report-only"]).toContain(
    "frame-ancestors 'none'",
  );
  expect(headers["content-security-policy-report-only"]).not.toContain(
    "'unsafe-eval'",
  );
});
