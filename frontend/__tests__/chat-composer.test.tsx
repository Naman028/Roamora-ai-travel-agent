import {
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Providers } from "@/app/providers";
import { ChatPanel } from "@/components/chat/chat-panel";
import { ClientChatError } from "@/features/chat/api/send-chat-message";

const mockSendChatMessage = vi.hoisted(() => vi.fn());

vi.mock("@/features/chat/api/send-chat-message", async (importOriginal) => {
  const original =
    await importOriginal<
      typeof import("@/features/chat/api/send-chat-message")
    >();
  return {
    ...original,
    sendChatMessage: mockSendChatMessage,
  };
});

function success(
  assistantMessage = "Your calm itinerary is ready.",
  selectedModel: "base" | "fine_tuned" = "fine_tuned",
) {
  return {
    data: {
      session_id: "demo-user-1",
      selected_model: selectedModel,
      adapter_loaded: selectedModel === "fine_tuned",
      parse_success: true,
      fallback_used: false,
      retry_used: false,
      assistant_message: assistantMessage,
      dashboard_payload: {
        schema_version: "travel_dashboard_v1" as const,
        intent: "itinerary_generation",
      },
    },
    requestId: "response-request-id",
  };
}

function renderChat() {
  return render(
    <Providers>
      <ChatPanel onOpenSavedTrips={() => undefined} />
    </Providers>,
  );
}

function composer() {
  return screen.getByRole("textbox", {
    name: "Ask Roamora about your travel idea",
  });
}

function modelSelector() {
  return screen.getByRole("combobox", { name: "Model" });
}

beforeEach(() => {
  mockSendChatMessage.mockReset();
  mockSendChatMessage.mockResolvedValue(success());
});

describe("ChatPanel Phase 3B flow", () => {
  it("shows the verified fine-tuned default in a keyboard-focusable selector", () => {
    renderChat();

    expect(modelSelector()).toHaveValue("fine_tuned");
    expect(screen.getByText("Roamora travel-tuned model")).toBeVisible();
    modelSelector().focus();
    expect(modelSelector()).toHaveFocus();
    expect(modelSelector()).toHaveAccessibleDescription(
      "Roamora travel-tuned model",
    );
  });

  it("populates the composer from a suggestion", () => {
    renderChat();
    fireEvent.click(
      screen.getByRole("button", { name: "Plan a slow weekend" }),
    );

    expect(composer()).toHaveValue("Plan a slow weekend");
    expect(
      screen.getByText(/kept only in memory and clears on refresh/i),
    ).toBeVisible();
  });

  it("uses Enter to submit exactly once and renders one assistant response", async () => {
    renderChat();
    fireEvent.change(composer(), {
      target: { value: "A quiet mountain break" },
    });
    fireEvent.keyDown(composer(), { key: "Enter", shiftKey: false });
    fireEvent.keyDown(composer(), { key: "Enter", shiftKey: false });

    await screen.findByText("Your calm itinerary is ready.");
    expect(mockSendChatMessage).toHaveBeenCalledTimes(1);
    expect(mockSendChatMessage).toHaveBeenCalledWith(
      {
        message: "A quiet mountain break",
        model_variant: "fine_tuned",
      },
      expect.any(AbortSignal),
    );
    expect(
      screen.getAllByText("Your calm itinerary is ready."),
    ).toHaveLength(1);
    expect(composer()).toHaveValue("");
  });

  it("keeps Shift+Enter available without submitting", () => {
    renderChat();
    fireEvent.change(composer(), {
      target: { value: "First line\nSecond line" },
    });
    fireEvent.keyDown(composer(), { key: "Enter", shiftKey: true });

    expect(composer()).toHaveValue("First line\nSecond line");
    expect(mockSendChatMessage).not.toHaveBeenCalled();
  });

  it("does not submit an empty message", () => {
    renderChat();
    fireEvent.change(composer(), { target: { value: "   " } });
    fireEvent.keyDown(composer(), { key: "Enter", shiftKey: false });
    expect(mockSendChatMessage).not.toHaveBeenCalled();
  });

  it("shows a pending state and prevents duplicate active requests", async () => {
    let resolveRequest!: (value: ReturnType<typeof success>) => void;
    mockSendChatMessage.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveRequest = resolve;
        }),
    );
    renderChat();
    fireEvent.change(composer(), { target: { value: "A slow journey" } });
    fireEvent.keyDown(composer(), { key: "Enter", shiftKey: false });

    expect(await screen.findByText("Planning…")).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Cancel travel request" }),
    ).toBeVisible();
    fireEvent.keyDown(composer(), { key: "Enter", shiftKey: false });
    expect(mockSendChatMessage).toHaveBeenCalledTimes(1);

    resolveRequest(success());
    await screen.findByText("Your calm itinerary is ready.");
  });

  it("preserves the draft and exposes safe retry details after failure", async () => {
    mockSendChatMessage.mockRejectedValueOnce(
      new ClientChatError(
        {
          code: "RATE_LIMITED",
          message: "Too many travel requests are active.",
          requestId: "safe-request-id",
          retryable: true,
          fieldErrors: [],
        },
        429,
      ),
    );
    renderChat();
    fireEvent.change(composer(), { target: { value: "Plan the coast" } });
    fireEvent.keyDown(composer(), { key: "Enter", shiftKey: false });

    expect(
      await screen.findByText("Too many travel requests are active."),
    ).toBeVisible();
    expect(composer()).toHaveValue("Plan the coast");
    expect(screen.getByRole("button", { name: "Try again" })).toBeVisible();
    expect(screen.queryByText("safe-request-id")).not.toBeVisible();
    fireEvent.click(screen.getByText("Technical details"));
    expect(screen.getByText("safe-request-id")).toBeVisible();
  });

  it("renders 422 field errors as safe text", async () => {
    mockSendChatMessage.mockRejectedValueOnce(
      new ClientChatError(
        {
          code: "VALIDATION_ERROR",
          message: "The chat request did not pass validation.",
          requestId: "validation-request",
          retryable: false,
          fieldErrors: [{ field: "message", message: "Message is too long." }],
        },
        422,
      ),
    );
    renderChat();
    fireEvent.change(composer(), { target: { value: "hello" } });
    fireEvent.keyDown(composer(), { key: "Enter", shiftKey: false });

    expect(await screen.findByText("Message is too long.")).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Try again" }),
    ).not.toBeInTheDocument();
  });

  it("cancels waiting without presenting a fake server failure", async () => {
    mockSendChatMessage.mockImplementation(
      (_input, signal: AbortSignal) =>
        new Promise((_, reject) => {
          signal.addEventListener("abort", () =>
            reject(
              new ClientChatError(
                {
                  code: "CLIENT_CANCELLED",
                  message: "You stopped waiting.",
                  requestId: "unavailable",
                  retryable: false,
                  fieldErrors: [],
                },
                null,
              ),
            ),
          );
        }),
    );
    renderChat();
    fireEvent.change(composer(), { target: { value: "A long plan" } });
    fireEvent.keyDown(composer(), { key: "Enter", shiftKey: false });
    fireEvent.click(
      await screen.findByRole("button", { name: "Cancel travel request" }),
    );

    expect(
      await screen.findByText(/backend work may still continue/i),
    ).toBeVisible();
    expect(screen.getByText("Waiting cancelled")).toBeVisible();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(composer()).toHaveValue("A long plan");
  });

  it("renders model text literally without executing raw HTML", async () => {
    mockSendChatMessage.mockResolvedValueOnce(
      success('<img src=x onerror="window.__unsafe=true">Safe text'),
    );
    renderChat();
    fireEvent.change(composer(), { target: { value: "hello" } });
    fireEvent.keyDown(composer(), { key: "Enter", shiftKey: false });

    expect(
      await screen.findByText(/<img src=x onerror=/),
    ).toBeVisible();
    expect(document.querySelector(".message__bubble img")).toBeNull();
  });

  it("requires a user action before retrying and does not auto-retry", async () => {
    mockSendChatMessage
      .mockRejectedValueOnce(
        new ClientChatError(
          {
            code: "GENERATION_TIMEOUT",
            message: "Travel generation took too long.",
            requestId: "timeout-id",
            retryable: true,
            fieldErrors: [],
          },
          504,
        ),
      )
      .mockResolvedValueOnce(success("Retry completed."));
    renderChat();
    fireEvent.change(composer(), { target: { value: "hello" } });
    fireEvent.keyDown(composer(), { key: "Enter", shiftKey: false });

    await screen.findByText("Travel generation took too long.");
    expect(mockSendChatMessage).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await screen.findByText("Retry completed.");
    expect(mockSendChatMessage).toHaveBeenCalledTimes(2);
  });

  it("captures the active model at submit and applies later changes only to the next request", async () => {
    let resolveFirst!: (value: ReturnType<typeof success>) => void;
    mockSendChatMessage
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveFirst = resolve;
          }),
      )
      .mockResolvedValueOnce(success("Base response.", "base"));
    renderChat();

    fireEvent.change(composer(), {
      target: { value: "First request" },
    });
    fireEvent.keyDown(composer(), { key: "Enter", shiftKey: false });
    await screen.findByRole("button", { name: "Cancel travel request" });
    fireEvent.change(modelSelector(), { target: { value: "base" } });

    expect(modelSelector()).toHaveValue("base");
    expect(mockSendChatMessage.mock.calls[0][0]).toEqual({
      message: "First request",
      model_variant: "fine_tuned",
    });

    resolveFirst(success("Fine-tuned response."));
    await screen.findByText("Fine-tuned response.");
    fireEvent.change(composer(), {
      target: { value: "Second request" },
    });
    fireEvent.keyDown(composer(), { key: "Enter", shiftKey: false });
    await screen.findByText("Base response.");

    expect(mockSendChatMessage.mock.calls[1][0]).toEqual({
      message: "Second request",
      model_variant: "base",
    });
    expect(screen.getByText("First request")).toBeVisible();
    expect(screen.getByText("Fine-tuned response.")).toBeVisible();
  });

  it("retries a failed request with its original model after the selector changes", async () => {
    mockSendChatMessage
      .mockRejectedValueOnce(
        new ClientChatError(
          {
            code: "SERVICE_UNAVAILABLE",
            message: "Travel planning is temporarily unavailable.",
            requestId: "failed-model-request",
            retryable: true,
            fieldErrors: [],
          },
          503,
        ),
      )
      .mockResolvedValueOnce(success("Original model retry completed."));
    renderChat();

    fireEvent.change(composer(), { target: { value: "Retry this plan" } });
    fireEvent.keyDown(composer(), { key: "Enter", shiftKey: false });
    await screen.findByText("Travel planning is temporarily unavailable.");
    fireEvent.change(modelSelector(), { target: { value: "base" } });
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await screen.findByText("Original model retry completed.");

    expect(mockSendChatMessage).toHaveBeenCalledTimes(2);
    expect(mockSendChatMessage.mock.calls[1][0]).toEqual({
      message: "Retry this plan",
      model_variant: "fine_tuned",
    });
    expect(modelSelector()).toHaveValue("base");
  });

  it("renders a model mismatch only as a safe failure and never as assistant success", async () => {
    mockSendChatMessage.mockRejectedValueOnce(
      new ClientChatError(
        {
          code: "MODEL_SELECTION_MISMATCH",
          message: "The chat service could not verify the requested model.",
          requestId: "mismatch-request",
          retryable: false,
          fieldErrors: [],
        },
        502,
      ),
    );
    renderChat();

    fireEvent.change(composer(), { target: { value: "Mismatch request" } });
    fireEvent.keyDown(composer(), { key: "Enter", shiftKey: false });

    expect(
      await screen.findByText(
        "The chat service could not verify the requested model.",
      ),
    ).toBeVisible();
    expect(screen.getByText("Not sent successfully")).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Try again" }),
    ).not.toBeInTheDocument();
    expect(mockSendChatMessage).toHaveBeenCalledTimes(1);
  });

  it("returns focus to the composer after completion", async () => {
    renderChat();
    fireEvent.change(composer(), { target: { value: "hello" } });
    fireEvent.keyDown(composer(), { key: "Enter", shiftKey: false });
    await screen.findByText("Your calm itinerary is ready.");
    await waitFor(() => expect(composer()).toHaveFocus());
  });
});
