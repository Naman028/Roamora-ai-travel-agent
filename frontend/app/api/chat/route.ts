import {
  CHAT_REQUEST_MAX_BYTES,
  chatRequestSchema,
  type ChatErrorResponse,
} from "@/features/chat/contracts/chat-contracts";
import {
  BackendChatError,
  sendBackendChat,
} from "@/server/backend/chat-client";
import {
  BodyLimitError,
  readLimitedBody,
} from "@/server/backend/read-limited-body";

export const runtime = "nodejs";
export const maxDuration = 130;

function responseHeaders(requestId: string, retryAfter?: string): HeadersInit {
  return {
    "Cache-Control": "no-store",
    "Content-Type": "application/json; charset=utf-8",
    "X-Request-ID": requestId,
    ...(retryAfter ? { "Retry-After": retryAfter } : {}),
  };
}

function errorResponse(
  status: number,
  requestId: string,
  code: string,
  message: string,
  retryable = false,
  fieldErrors: Array<{ field: string; message: string }> = [],
  retryAfter?: string,
): Response {
  const payload: ChatErrorResponse = {
    error: {
      code,
      message,
      requestId,
      retryable,
      fieldErrors,
    },
  };
  return new Response(JSON.stringify(payload), {
    status,
    headers: responseHeaders(requestId, retryAfter),
  });
}

function isJsonContentType(value: string | null): boolean {
  return (
    value !== null &&
    /^application\/json(?:\s*;\s*charset=[\w-]+)?$/i.test(value)
  );
}

export async function POST(request: Request): Promise<Response> {
  const requestId = crypto.randomUUID();

  if (!isJsonContentType(request.headers.get("Content-Type"))) {
    return errorResponse(
      415,
      requestId,
      "UNSUPPORTED_MEDIA_TYPE",
      "Chat requests must use application/json.",
    );
  }

  let requestText: string;
  try {
    requestText = await readLimitedBody(
      request.body,
      CHAT_REQUEST_MAX_BYTES,
      request.headers.get("Content-Length"),
    );
  } catch (error) {
    if (error instanceof BodyLimitError) {
      return errorResponse(
        413,
        requestId,
        "REQUEST_TOO_LARGE",
        "The chat request is too large.",
      );
    }
    return errorResponse(
      400,
      requestId,
      "INVALID_REQUEST_BODY",
      "The chat request body could not be read.",
    );
  }

  if (!requestText.trim()) {
    return errorResponse(
      400,
      requestId,
      "MISSING_REQUEST_BODY",
      "A JSON request body is required.",
    );
  }

  let requestPayload: unknown;
  try {
    requestPayload = JSON.parse(requestText);
  } catch {
    return errorResponse(
      400,
      requestId,
      "INVALID_JSON",
      "The chat request contains invalid JSON.",
    );
  }

  const parsedRequest = chatRequestSchema.safeParse(requestPayload);
  if (!parsedRequest.success) {
    const fieldErrors = parsedRequest.error.issues.map((issue) => ({
      field: issue.path.join(".") || "body",
      message: issue.message,
    }));
    return errorResponse(
      422,
      requestId,
      "VALIDATION_ERROR",
      "The chat request did not pass validation.",
      false,
      fieldErrors,
    );
  }

  try {
    const payload = await sendBackendChat(
      parsedRequest.data,
      requestId,
      request.signal,
    );
    return new Response(JSON.stringify(payload), {
      status: 200,
      headers: responseHeaders(requestId),
    });
  } catch (error) {
    if (error instanceof BackendChatError) {
      return errorResponse(
        error.status,
        requestId,
        error.code,
        error.message,
        error.retryable,
        error.fieldErrors,
        error.retryAfter,
      );
    }
    return errorResponse(
      500,
      requestId,
      "BFF_INTERNAL_ERROR",
      "The chat request could not be completed.",
      true,
    );
  }
}
