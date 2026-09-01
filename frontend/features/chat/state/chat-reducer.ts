import type {
  ChatMessage,
  ChatState,
} from "@/features/chat/types/chat.types";
import type { ChatErrorResponse } from "@/features/chat/contracts/chat-contracts";

const onboardingMessage: ChatMessage = {
  id: "roamora-onboarding",
  role: "assistant",
  kind: "onboarding",
  content:
    "Welcome to Roamora. I’ll help turn the way you like to travel into a clear, considered plan.",
  status: "completed",
};

export const initialChatState: ChatState = {
  messages: [onboardingMessage],
  submissionStatus: "idle",
  activeUserMessageId: null,
  error: null,
};

type ChatAction =
  | { type: "submit"; message: ChatMessage }
  | { type: "retry"; userMessageId: string }
  | {
      type: "succeed";
      userMessageId: string;
      assistantMessage: ChatMessage;
    }
  | {
      type: "fail";
      userMessageId: string;
      error: ChatErrorResponse["error"];
    }
  | { type: "cancel"; userMessageId: string }
  | { type: "dismiss-error" };

function updateMessage(
  messages: ChatMessage[],
  id: string,
  update: Partial<ChatMessage>,
): ChatMessage[] {
  return messages.map((message) =>
    message.id === id ? { ...message, ...update } : message,
  );
}

export function chatReducer(
  state: ChatState,
  action: ChatAction,
): ChatState {
  switch (action.type) {
    case "submit":
      if (state.submissionStatus === "submitting") {
        return state;
      }
      return {
        messages: [...state.messages, action.message],
        submissionStatus: "submitting",
        activeUserMessageId: action.message.id,
        error: null,
      };
    case "retry":
      if (state.submissionStatus === "submitting") {
        return state;
      }
      return {
        ...state,
        messages: updateMessage(state.messages, action.userMessageId, {
          status: "pending",
        }),
        submissionStatus: "submitting",
        activeUserMessageId: action.userMessageId,
        error: null,
      };
    case "succeed":
      return {
        messages: [
          ...updateMessage(state.messages, action.userMessageId, {
            status: "completed",
          }),
          action.assistantMessage,
        ],
        submissionStatus: "completed",
        activeUserMessageId: null,
        error: null,
      };
    case "fail":
      return {
        ...state,
        messages: updateMessage(state.messages, action.userMessageId, {
          status: "failed",
          requestId: action.error.requestId,
        }),
        submissionStatus: "failed",
        activeUserMessageId: null,
        error: action.error,
      };
    case "cancel":
      return {
        ...state,
        messages: updateMessage(state.messages, action.userMessageId, {
          status: "cancelled",
        }),
        submissionStatus: "cancelled",
        activeUserMessageId: null,
        error: null,
      };
    case "dismiss-error":
      return { ...state, error: null };
  }
}
