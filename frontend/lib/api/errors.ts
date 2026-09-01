import type { NormalizedFrontendError } from "@/lib/domain/models";

const SAFE_FALLBACK_MESSAGE =
  "Something interrupted the request. Please try again.";

export function normalizeFrontendError(
  error: unknown,
): NormalizedFrontendError {
  if (
    typeof error === "object" &&
    error !== null &&
    "status" in error &&
    typeof error.status === "number"
  ) {
    const status = error.status;
    return {
      status,
      code: `HTTP_${status}`,
      message: SAFE_FALLBACK_MESSAGE,
      retryable: status >= 500 || status === 408 || status === 429,
    };
  }

  return {
    status: null,
    code: "UNEXPECTED_ERROR",
    message: SAFE_FALLBACK_MESSAGE,
    retryable: true,
  };
}

export function isRetryableError(error: unknown): boolean {
  return normalizeFrontendError(error).retryable;
}
