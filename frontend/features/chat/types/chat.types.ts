import type {
  ChatErrorResponse,
  ChatRequest,
  ChatResponse,
} from "@/features/chat/contracts/chat-contracts";

export type MessageStatus =
  | "pending"
  | "completed"
  | "failed"
  | "cancelled";

export interface ChatMessage {
  id: string;
  role: "assistant" | "user";
  content: string;
  kind: "onboarding" | "conversation";
  status: MessageStatus;
  requestId?: string;
}

export interface ChatState {
  messages: ChatMessage[];
  submissionStatus:
    | "idle"
    | "submitting"
    | "completed"
    | "failed"
    | "cancelled";
  activeUserMessageId: string | null;
  error: ChatErrorResponse["error"] | null;
}

export interface SendChatVariables {
  input: ChatRequest;
  signal: AbortSignal;
}

export interface ChatSuccess {
  data: ChatResponse;
  requestId: string;
}
