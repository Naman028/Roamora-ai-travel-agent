"use client";

import { useReducer, useRef, useState } from "react";
import { ChatComposer } from "@/components/chat/chat-composer";
import { ChatHeader } from "@/components/chat/chat-header";
import { MessageList } from "@/components/chat/message-list";
import { SuggestionChips } from "@/components/chat/suggestion-chips";
import { ClientChatError } from "@/features/chat/api/send-chat-message";
import { useChatMutation } from "@/features/chat/hooks/use-chat-mutation";
import {
  chatReducer,
  initialChatState,
} from "@/features/chat/state/chat-reducer";

export function ChatPanel({
  onOpenSavedTrips,
}: {
  onOpenSavedTrips: () => void;
}) {
  const [composerValue, setComposerValue] = useState("");
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

  async function runSubmission(value: string, existingUserMessageId?: string) {
    if (
      activeController.current ||
      (!existingUserMessageId && cancelledDraftBlocked.current)
    ) {
      return;
    }

    const message = value.trim();
    if (!message) {
      return;
    }

    const userMessageId = existingUserMessageId ?? crypto.randomUUID();
    const controller = new AbortController();
    activeController.current = controller;

    if (existingUserMessageId) {
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
        },
      });
    }

    try {
      const result = await mutation.mutateAsync({
        input: { message },
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
    .find((message) => message.status === "failed");

  return (
    <section
      className="chat-panel"
      aria-label="Roamora AI conversation"
      data-testid="chat-panel"
    >
      <ChatHeader onOpenSavedTrips={onOpenSavedTrips} />
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
                      failedMessage.id,
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
