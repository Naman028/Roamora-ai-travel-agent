"use client";

import { useReducer, useRef, useState } from "react";
import { ChatComposer } from "@/components/chat/chat-composer";
import { ChatHeader } from "@/components/chat/chat-header";
import { MessageList } from "@/components/chat/message-list";
import { SuggestionChips } from "@/components/chat/suggestion-chips";
import { ClientChatError } from "@/features/chat/api/send-chat-message";
import { useChatMutation } from "@/features/chat/hooks/use-chat-mutation";
import type { ChatMessage } from "@/features/chat/types/chat.types";
import {
  chatReducer,
  initialChatState,
} from "@/features/chat/state/chat-reducer";
import {
  DEFAULT_MODEL_VARIANT,
  type ModelVariant,
} from "@/features/model-selection/contracts/model-variant";

function hasRequestedModel(
  message: ChatMessage,
): message is ChatMessage & { requestedModel: ModelVariant } {
  return message.requestedModel !== undefined;
}

export function ChatPanel({
  onOpenSavedTrips,
}: {
  onOpenSavedTrips: () => void;
}) {
  const [composerValue, setComposerValue] = useState("");
  const [selectedModel, setSelectedModel] = useState<ModelVariant>(
    DEFAULT_MODEL_VARIANT,
  );
  const [chatState, dispatch] = useReducer(chatReducer, initialChatState);
  const activeController = useRef<AbortController | null>(null);
  const cancelledDraftBlocked = useRef(false);
  const mutation = useChatMutation();
  const isSubmitting = chatState.submissionStatus === "submitting";

  function selectSuggestion(suggestion: string) {
    cancelledDraftBlocked.current = false;
    setComposerValue(suggestion);
    requestAnimationFrame(() => {
      document.querySelector<HTMLTextAreaElement>("#travel-message")?.focus();
    });
  }

  async function runSubmission(
    value: string,
    existingRequest?: {
      userMessageId: string;
      requestedModel: ModelVariant;
    },
  ) {
    if (
      activeController.current ||
      (!existingRequest && cancelledDraftBlocked.current)
    ) {
      return;
    }

    const message = value.trim();
    if (!message) {
      return;
    }

    const requestedModel = existingRequest?.requestedModel ?? selectedModel;
    const userMessageId =
      existingRequest?.userMessageId ?? crypto.randomUUID();
    const controller = new AbortController();
    activeController.current = controller;

    if (existingRequest) {
      dispatch({ type: "retry", userMessageId });
    } else {
      dispatch({
        type: "submit",
        message: {
          id: userMessageId,
          role: "user",
          kind: "conversation",
          content: message,
          status: "pending",
          requestedModel,
        },
      });
    }

    try {
      const result = await mutation.mutateAsync({
        input: { message, model_variant: requestedModel },
        signal: controller.signal,
      });
      dispatch({
        type: "succeed",
        userMessageId,
        assistantMessage: {
          id: crypto.randomUUID(),
          role: "assistant",
          kind: "conversation",
          content: result.data.assistant_message,
          status: "completed",
          requestId: result.requestId,
          requestedModel,
        },
      });
      setComposerValue("");
      cancelledDraftBlocked.current = false;
    } catch (error) {
      if (
        error instanceof ClientChatError &&
        error.details.code === "CLIENT_CANCELLED"
      ) {
        dispatch({ type: "cancel", userMessageId });
      } else {
        const details =
          error instanceof ClientChatError
            ? error.details
            : {
                code: "UNEXPECTED_ERROR",
                message: "The chat request could not be completed.",
                requestId: "unavailable",
                retryable: true,
                fieldErrors: [],
              };
        dispatch({ type: "fail", userMessageId, error: details });
      }
    } finally {
      activeController.current = null;
      requestAnimationFrame(() => {
        document.querySelector<HTMLTextAreaElement>("#travel-message")?.focus();
      });
    }
  }

  function cancelSubmission() {
    cancelledDraftBlocked.current = true;
    activeController.current?.abort();
  }

  function updateComposerValue(value: string) {
    cancelledDraftBlocked.current = false;
    setComposerValue(value);
  }

  const failedMessage = [...chatState.messages]
    .reverse()
    .find(
      (message): message is ChatMessage & { requestedModel: ModelVariant } =>
        message.status === "failed" && hasRequestedModel(message),
    );

  return (
    <section
      className="chat-panel"
      aria-label="Roamora AI conversation"
      data-testid="chat-panel"
    >
      <ChatHeader
        onOpenSavedTrips={onOpenSavedTrips}
        selectedModel={selectedModel}
        onSelectModel={setSelectedModel}
      />
      <div className="chat-panel__body">
        <MessageList messages={chatState.messages} />
      </div>
      <div className="chat-panel__footer">
        {chatState.error ? (
          <div className="chat-error" role="alert">
            <p>{chatState.error.message}</p>
            {chatState.error.fieldErrors.length > 0 ? (
              <ul>
                {chatState.error.fieldErrors.map((fieldError) => (
                  <li key={`${fieldError.field}-${fieldError.message}`}>
                    {fieldError.message}
                  </li>
                ))}
              </ul>
            ) : null}
            <div className="chat-error__actions">
              {chatState.error.retryable && failedMessage ? (
                <button
                  type="button"
                  onClick={() =>
                    void runSubmission(
                      failedMessage.content,
                      {
                        userMessageId: failedMessage.id,
                        requestedModel: failedMessage.requestedModel,
                      },
                    )
                  }
                >
                  Try again
                </button>
              ) : null}
              <button
                type="button"
                onClick={() => {
                  dispatch({ type: "dismiss-error" });
                  document
                    .querySelector<HTMLTextAreaElement>("#travel-message")
                    ?.focus();
                }}
              >
                Edit message
              </button>
            </div>
            {chatState.error.requestId !== "unavailable" ? (
              <details>
                <summary>Technical details</summary>
                <p>
                  Request ID:{" "}
                  <span className="request-id">
                    {chatState.error.requestId}
                  </span>
                </p>
              </details>
            ) : null}
          </div>
        ) : null}
        <SuggestionChips
          onSelect={selectSuggestion}
          disabled={isSubmitting}
        />
        <ChatComposer
          value={composerValue}
          submissionStatus={chatState.submissionStatus}
          onChange={updateComposerValue}
          onSubmit={() => void runSubmission(composerValue)}
          onCancel={cancelSubmission}
        />
      </div>
    </section>
  );
}
