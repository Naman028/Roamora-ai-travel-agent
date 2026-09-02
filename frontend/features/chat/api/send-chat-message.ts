import {
  chatErrorResponseSchema,
  chatResponseSchema,
  type ChatErrorResponse,
  type ChatRequest,
} from "@/features/chat/contracts/chat-contracts";
import type { ChatSuccess } from "@/features/chat/types/chat.types";
import {
  CLIENT_MODEL_SELECTION_MISMATCH_MESSAGE,
  MODEL_SELECTION_MISMATCH_CODE,
} from "@/features/model-selection/contracts/model-selection-errors";

const SAFE_CLIENT_ERROR: ChatErrorResponse["error"] = {
  code: "INVALID_BFF_RESPONSE",
  message: "The chat service returned an invalid response.",
  requestId: "unavailable",
  retryable: true,
  fieldErrors: [],
};

export class ClientChatError extends Error {
  constructor(
    readonly details: ChatErrorResponse["error"],
    readonly status: number | null,
  ) {
    super(details.message);
    this.name = "ClientChatError";
  }
}

function requestIdFrom(response: Response): string {
  return response.headers.get("X-Request-ID") ?? "unavailable";
}

export async function sendChatMessage(
  input: ChatRequest,
  signal: AbortSignal,
): Promise<ChatSuccess> {
  let response: Response;
  try {
    response = await fetch("/api/chat", {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify(input),
      signal,
      cache: "no-store",
    });
  } catch {
    if (signal.aborted) {
      throw new ClientChatError(
        {
          code: "CLIENT_CANCELLED",
          message:
            "You stopped waiting. This does not guarantee backend generation stopped.",
          requestId: "unavailable",
          retryable: false,
          fieldErrors: [],
        },
        null,
      );
    }
    throw new ClientChatError(
      {
        code: "NETWORK_ERROR",
        message: "The chat service could not be reached.",
        requestId: "unavailable",
        retryable: true,
        fieldErrors: [],
      },
      null,
    );
  }

  const requestId = requestIdFrom(response);
  let payload: unknown;
  try {
    payload = JSON.parse(await response.text());
  } catch {
    throw new ClientChatError(
      { ...SAFE_CLIENT_ERROR, requestId },
      response.status,
    );
  }

  if (response.ok) {
    const parsed = chatResponseSchema.safeParse(payload);
    if (!parsed.success) {
      throw new ClientChatError(
        { ...SAFE_CLIENT_ERROR, requestId },
        response.status,
      );
    }
    if (parsed.data.selected_model !== input.model_variant) {
      throw new ClientChatError(
        {
          code: MODEL_SELECTION_MISMATCH_CODE,
          message: CLIENT_MODEL_SELECTION_MISMATCH_MESSAGE,
          requestId,
          retryable: false,
          fieldErrors: [],
        },
        502,
      );
    }
    return { data: parsed.data, requestId };
  }

  const parsedError = chatErrorResponseSchema.safeParse(payload);
  if (!parsedError.success) {
    throw new ClientChatError(
      { ...SAFE_CLIENT_ERROR, requestId },
      response.status,
    );
  }

  throw new ClientChatError(
    {
      ...parsedError.data.error,
      requestId,
    },
    response.status,
  );
}
