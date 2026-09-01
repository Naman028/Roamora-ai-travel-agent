import "server-only";

import {
  backendErrorResponseSchema,
  BFF_CHAT_TIMEOUT_MS,
  CHAT_RESPONSE_MAX_BYTES,
  chatResponseSchema,
  type ChatRequest,
  type ChatResponse,
} from "@/features/chat/contracts/chat-contracts";
import {
  BodyLimitError,
  readLimitedBody,
} from "@/server/backend/read-limited-body";
import { readBackendEnv } from "@/server/env/backend-env";

type FailureKind =
  | "client_cancelled"
  | "configuration"
  | "network"
  | "timeout"
  | "upstream";

export class BackendChatError extends Error {
  constructor(
    readonly kind: FailureKind,
    readonly status: number,
    readonly code: string,
    message: string,
    readonly retryable: boolean,
    readonly fieldErrors: Array<{ field: string; message: string }> = [],
    readonly retryAfter?: string,
  ) {
    super(message);
    this.name = "BackendChatError";
  }
}

function isJsonContentType(value: string | null): boolean {
  return (
    value !== null &&
    /^application\/json(?:\s*;\s*charset=[\w-]+)?$/i.test(value)
  );
}

function safeRetryAfter(value: string | null): string | undefined {
  return value && /^\d{1,5}$/.test(value) ? value : undefined;
}

function protocolFailure(message: string): BackendChatError {
  return new BackendChatError(
    "upstream",
    502,
    "INVALID_UPSTREAM_RESPONSE",
    message,
    true,
  );
}

function mapBackendError(
  status: number,
  code: string,
  retryable: boolean,
  fieldErrors: Array<{ field: string; message: string }>,
  retryAfter?: string,
): BackendChatError {
  if (status === 401) {
    return new BackendChatError(
      "configuration",
      503,
      "UPSTREAM_CONFIGURATION_ERROR",
      "Travel planning is temporarily unavailable because the service is not configured correctly.",
      false,
    );
  }

  const mapped = {
    422: {
      code: "VALIDATION_ERROR",
      message: "The travel request did not pass validation.",
      retryable: false,
    },
    429: {
      code: code === "CONCURRENCY_LIMITED" ? code : "RATE_LIMITED",
      message: "Too many travel requests are active. Please wait before trying again.",
      retryable: true,
    },
    500: {
      code: "GENERATION_FAILED",
      message: "Travel generation could not be completed.",
      retryable: true,
    },
    502: {
      code: "UPSTREAM_GENERATION_ERROR",
      message: "The travel service returned an invalid result.",
      retryable: true,
    },
    503: {
      code: "SERVICE_UNAVAILABLE",
      message: "Travel planning is temporarily unavailable.",
      retryable,
    },
    504: {
      code: "GENERATION_TIMEOUT",
      message:
        "Travel generation took too long. It may still have consumed backend work.",
      retryable: true,
    },
  }[status];

  if (!mapped) {
    return protocolFailure("The travel service returned an unexpected status.");
  }

  return new BackendChatError(
    "upstream",
    status,
    mapped.code,
    mapped.message,
    mapped.retryable,
    status === 422 ? fieldErrors : [],
    status === 429 ? retryAfter : undefined,
  );
}

export async function sendBackendChat(
  input: ChatRequest,
  requestId: string,
  clientSignal: AbortSignal,
): Promise<ChatResponse> {
  let backendEnv: ReturnType<typeof readBackendEnv>;
  try {
    backendEnv = readBackendEnv();
  } catch {
    throw new BackendChatError(
      "configuration",
      503,
      "BFF_CONFIGURATION_ERROR",
      "Travel planning is not configured on this server.",
      false,
    );
  }

  const controller = new AbortController();
  const clientAbortReason = { kind: "client" };
  const timeoutAbortReason = { kind: "timeout" };
  const handleClientAbort = () => {
    if (!controller.signal.aborted) {
      controller.abort(clientAbortReason);
    }
  };
  clientSignal.addEventListener("abort", handleClientAbort, { once: true });
  if (clientSignal.aborted) {
    handleClientAbort();
  }
  const timeoutId = setTimeout(() => {
    if (!controller.signal.aborted) {
      controller.abort(timeoutAbortReason);
    }
  }, BFF_CHAT_TIMEOUT_MS);

  try {
    const response = await fetch(`${backendEnv.baseUrl}/chat`, {
      method: "POST",
      cache: "no-store",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-API-Key": backendEnv.apiKey,
        "X-Request-ID": requestId,
      },
      body: JSON.stringify(input),
      signal: controller.signal,
    });

    if (response.headers.get("X-Request-ID") !== requestId) {
      throw protocolFailure(
        "The travel service returned invalid correlation metadata.",
      );
    }
    if (!isJsonContentType(response.headers.get("Content-Type"))) {
      throw protocolFailure(
        "The travel service returned an unsupported content type.",
      );
    }

    let responseText: string;
    try {
      responseText = await readLimitedBody(
        response.body,
        CHAT_RESPONSE_MAX_BYTES,
        response.headers.get("Content-Length"),
      );
    } catch (error) {
      if (error instanceof BodyLimitError) {
        throw protocolFailure("The travel service response was too large.");
      }
      throw protocolFailure("The travel service response was unreadable.");
    }

    let payload: unknown;
    try {
      payload = JSON.parse(responseText);
    } catch {
      throw protocolFailure("The travel service returned invalid JSON.");
    }

    if (response.ok) {
      const parsed = chatResponseSchema.safeParse(payload);
      if (!parsed.success) {
        throw protocolFailure(
          "The travel service response did not match its contract.",
        );
      }
      return parsed.data;
    }

    const parsedError = backendErrorResponseSchema.safeParse(payload);
    if (
      !parsedError.success ||
      parsedError.data.error.request_id !== requestId
    ) {
      throw protocolFailure(
        "The travel service returned an invalid error response.",
      );
    }

    throw mapBackendError(
      response.status,
      parsedError.data.error.code,
      parsedError.data.error.retryable ?? false,
      parsedError.data.error.field_errors ?? [],
      safeRetryAfter(response.headers.get("Retry-After")),
    );
  } catch (error) {
    if (error instanceof BackendChatError) {
      throw error;
    }
    if (controller.signal.reason === clientAbortReason) {
      throw new BackendChatError(
        "client_cancelled",
        499,
        "CLIENT_CANCELLED",
        "The browser stopped waiting for this request.",
        false,
      );
    }
    if (controller.signal.reason === timeoutAbortReason) {
      throw new BackendChatError(
        "timeout",
        504,
        "BFF_TIMEOUT",
        "Travel generation took too long. It may still have consumed backend work.",
        true,
      );
    }
    throw new BackendChatError(
      "network",
      503,
      "BACKEND_UNAVAILABLE",
      "The travel service could not be reached.",
      true,
    );
  } finally {
    clearTimeout(timeoutId);
    clientSignal.removeEventListener("abort", handleClientAbort);
  }
}
