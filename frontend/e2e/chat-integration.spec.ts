import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page, type Route } from "@playwright/test";

function successBody(
  assistantMessage = "Your thoughtful travel plan is ready.",
  selectedModel: "base" | "fine_tuned" = "fine_tuned",
) {
  return {
    session_id: "demo-user-1",
    selected_model: selectedModel,
    adapter_loaded: selectedModel === "fine_tuned",
    parse_success: true,
    fallback_used: false,
    retry_used: false,
    assistant_message: assistantMessage,
    dashboard_payload: {
      schema_version: "travel_dashboard_v1",
      intent: "itinerary_generation",
    },
  };
}

async function fulfillSuccess(
  route: Route,
  assistantMessage?: string,
  selectedModel: "base" | "fine_tuned" = "fine_tuned",
) {
  await route.fulfill({
    status: 200,
    contentType: "application/json",
    headers: { "X-Request-ID": "e2e-chat-request-id" },
    body: JSON.stringify(successBody(assistantMessage, selectedModel)),
  });
}

async function openChat(page: Page) {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { level: 1, name: "Roamora AI" }),
  ).toBeVisible();
}

test("one same-origin chat request shows pending then a safe response", async ({
  page,
}) => {
  let requestCount = 0;
  let releaseResponse: (() => void) | undefined;
  const responseGate = new Promise<void>((resolve) => {
    releaseResponse = resolve;
  });
  const observedUrls: string[] = [];
  page.on("request", (request) => observedUrls.push(request.url()));
  await page.route("**/api/chat", async (route) => {
    requestCount += 1;
    expect(route.request().method()).toBe("POST");
    expect(route.request().headers()["x-api-key"]).toBeUndefined();
    expect(route.request().url()).toBe("http://127.0.0.1:3000/api/chat");
    expect(route.request().postDataJSON()).toEqual({
      message: "Plan a quiet rail trip",
      model_variant: "fine_tuned",
    });
    await responseGate;
    await fulfillSuccess(route);
  });

  await openChat(page);
  const composer = page.getByRole("textbox", {
    name: "Ask Roamora about your travel idea",
  });
  await composer.fill("Plan a quiet rail trip");
  await composer.press("Enter");
  await expect(page.getByText("Planning…")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Cancel travel request" }),
  ).toBeVisible();
  await composer.press("Enter");
  expect(requestCount).toBe(1);

  releaseResponse?.();
  await expect(
    page.getByText("Your thoughtful travel plan is ready."),
  ).toBeVisible();
  await expect(composer).toHaveValue("");
  expect(requestCount).toBe(1);
  expect(
    observedUrls.some((url) => url.includes("127.0.0.1:8000/chat")),
  ).toBe(false);
});

test("the keyboard-operable selector sends the exact base transport value", async ({
  page,
}) => {
  let observedBody: unknown;
  await page.route("**/api/chat", async (route) => {
    observedBody = route.request().postDataJSON();
    await fulfillSuccess(route, "Base model response.", "base");
  });
  await openChat(page);

  const selector = page.getByRole("combobox", { name: "Model" });
  await expect(selector).toHaveValue("fine_tuned");
  await selector.focus();
  await page.keyboard.press("ArrowDown");
  await expect(selector).toHaveValue("base");
  await expect(selector).toBeFocused();

  const composer = page.getByRole("textbox", {
    name: "Ask Roamora about your travel idea",
  });
  await composer.fill("Compare with the original model");
  await composer.press("Enter");
  await expect(page.getByText("Base model response.")).toBeVisible();
  expect(observedBody).toEqual({
    message: "Compare with the original model",
    model_variant: "base",
  });
});

test("a mismatched success is shown only as a safe failure", async ({ page }) => {
  let requestCount = 0;
  await page.route("**/api/chat", async (route) => {
    requestCount += 1;
    await fulfillSuccess(route, "This must not render.", "base");
  });
  await openChat(page);

  const composer = page.getByRole("textbox", {
    name: "Ask Roamora about your travel idea",
  });
  await composer.fill("Keep the selected model truthful");
  await composer.press("Enter");

  await expect(
    page.getByText("The chat service could not verify the requested model."),
  ).toBeVisible();
  await expect(page.getByText("This must not render.")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Try again" })).toHaveCount(0);
  expect(requestCount).toBe(1);
});

for (const scenario of [
  {
    status: 422,
    code: "VALIDATION_ERROR",
    message: "The chat request did not pass validation.",
    fieldErrors: [{ field: "message", message: "Please shorten the message." }],
  },
  {
    status: 429,
    code: "RATE_LIMITED",
    message: "Too many travel requests are active.",
    fieldErrors: [],
  },
  {
    status: 503,
    code: "SERVICE_UNAVAILABLE",
    message: "Travel planning is temporarily unavailable.",
    fieldErrors: [],
  },
]) {
  test(`presents a safe ${scenario.status} response`, async ({ page }) => {
    await page.route("**/api/chat", async (route) => {
      await route.fulfill({
        status: scenario.status,
        contentType: "application/json",
        headers: { "X-Request-ID": `e2e-${scenario.status}-request` },
        body: JSON.stringify({
          error: {
            code: scenario.code,
            message: scenario.message,
            requestId: `e2e-${scenario.status}-request`,
            retryable: scenario.status !== 422,
            fieldErrors: scenario.fieldErrors,
          },
        }),
      });
    });
    await openChat(page);
    const composer = page.getByRole("textbox", {
      name: "Ask Roamora about your travel idea",
    });
    await composer.fill("Keep this editable");
    await composer.press("Enter");

    await expect(page.getByText(scenario.message)).toBeVisible();
    await expect(composer).toHaveValue("Keep this editable");
    if (scenario.status === 422) {
      await expect(page.getByText("Please shorten the message.")).toBeVisible();
    }
    await expect(page.getByText(`e2e-${scenario.status}-request`)).toBeHidden();
  });
}

test("cancelling stops browser waiting without claiming backend work stopped", async ({
  page,
}) => {
  let releaseResponse: (() => void) | undefined;
  const responseGate = new Promise<void>((resolve) => {
    releaseResponse = resolve;
  });
  await page.route("**/api/chat", async (route) => {
    await responseGate;
    await route.abort("aborted");
  });
  await openChat(page);
  const composer = page.getByRole("textbox", {
    name: "Ask Roamora about your travel idea",
  });
  await composer.fill("A generation I may cancel");
  await composer.press("Enter");
  await page.getByRole("button", { name: "Cancel travel request" }).click();
  releaseResponse?.();

  await expect(
    page.getByText("You stopped waiting. Backend work may still continue."),
  ).toBeVisible();
  await expect(page.getByText("Waiting cancelled")).toBeVisible();
  await expect(composer).toHaveValue("A generation I may cancel");
  await expect(page.locator(".chat-error")).toHaveCount(0);
});

test("assistant HTML is rendered literally and the integrated view stays Axe-clean", async ({
  page,
}) => {
  await page.route("**/api/chat", (route) =>
    fulfillSuccess(
      route,
      '<img src=x onerror="window.__roamoraUnsafe=true">Plain text',
    ),
  );
  await openChat(page);
  const composer = page.getByRole("textbox", {
    name: "Ask Roamora about your travel idea",
  });
  await composer.fill("Test safe output");
  await composer.press("Enter");
  await expect(page.getByText(/<img src=x onerror=/)).toBeVisible();

  expect(await page.locator(".message__bubble img").count()).toBe(0);
  expect(
    await page.evaluate(
      () =>
        "__roamoraUnsafe" in window &&
        Boolean(
          (
            window as Window & {
              __roamoraUnsafe?: boolean;
            }
          ).__roamoraUnsafe,
        ),
    ),
  ).toBe(false);
  await expect(
    page.getByRole("heading", { name: "No trip in motion yet" }),
  ).toBeVisible();
  await expect(page.getByRole("heading", { name: "A fresh page" })).toBeVisible();
  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations).toEqual([]);
});
