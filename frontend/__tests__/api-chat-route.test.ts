import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

import * as chatRoute from "@/app/api/chat/route";
import {
  BFF_CHAT_TIMEOUT_MS,
  CHAT_REQUEST_MAX_BYTES,
  CHAT_RESPONSE_MAX_BYTES,
} from "@/features/chat/contracts/chat-contracts";

const backendUrl = "http://127.0.0.1:8000";
const backendKey = "server-only-test-key";

function validSuccess() {
  return {
    session_id: "demo-user-1",
    selected_model: "fine_tuned",
    adapter_loaded: true,
    parse_success: true,
    fallback_used: false,
    retry_used: false,
    assistant_message: "A safe itinerary response.",
    dashboard_payload: {
      schema_version: "travel_dashboard_v1",
      intent: "itinerary_generation",
    },
  };
}

function request(
  body: string,
  options: {
    contentType?: string;
    headers?: Record<string, string>;
    signal?: AbortSignal;
  } = {},
) {
  return new Request("http://localhost/api/chat", {
    method: "POST",
    headers: {
      "Content-Type": options.contentType ?? "application/json",
      ...options.headers,
    },
    body,
    signal: options.signal,
  });
}

function upstreamJson(
  body: object | string,
  requestId: string,
  status = 200,
  extraHeaders: Record<string, string> = {},
) {
  return new Response(
    typeof body === "string" ? body : JSON.stringify(body),
    {
      status,
      headers: {
        "Content-Type": "application/json",
        "X-Request-ID": requestId,
        ...extraHeaders,
      },
    },
  );
}

function mockSuccessfulBackend() {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (_, init) => {
    const headers = new Headers(init?.headers);
    return upstreamJson(
      validSuccess(),
      headers.get("X-Request-ID") ?? "missing",
    );
  });
}

beforeEach(() => {
  process.env.BACKEND_API_URL = backendUrl;
  process.env.BACKEND_API_KEY = backendKey;
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  delete process.env.BACKEND_API_URL;
  delete process.env.BACKEND_API_KEY;
});

describe("POST /api/chat", () => {
  it("returns a validated success with no-store and a generated request ID", async () => {
    mockSuccessfulBackend();
    const response = await chatRoute.POST(
      request(JSON.stringify({ message: "Plan a calm weekend" })),
    );

    expect(response.status).toBe(200);
    expect(response.headers.get("Cache-Control")).toBe("no-store");
    expect(response.headers.get("X-Request-ID")).toMatch(
      /^[0-9a-f-]{36}$/i,
    );
    await expect(response.json()).resolves.toEqual(validSuccess());
  });

  it("adds the server key, fixed URL, request ID, and minimum headers upstream", async () => {
    const fetchSpy = mockSuccessfulBackend();
    const response = await chatRoute.POST(
      request(JSON.stringify({ message: "Plan by train" }), {
        headers: {
          "X-API-Key": "browser-forgery",
          "X-Forwarded-For": "203.0.113.5",
          Cookie: "private=cookie",
        },
      }),
    );
    const [url, init] = fetchSpy.mock.calls[0];
    const headers = new Headers(init?.headers);

    expect(response.status).toBe(200);
    expect(url).toBe(`${backendUrl}/chat`);
    expect(headers.get("X-API-Key")).toBe(backendKey);
    expect(headers.get("X-API-Key")).not.toBe("browser-forgery");
    expect(headers.get("X-Forwarded-For")).toBeNull();
    expect(headers.get("Cookie")).toBeNull();
    expect(headers.get("X-Request-ID")).toBe(
      response.headers.get("X-Request-ID"),
    );
    expect(init?.cache).toBe("no-store");
  });

  it("rejects a request-supplied backend URL or raw-output flag", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    for (const extra of [
      { backend_url: "https://attacker.example" },
      { include_raw_model_output: false },
    ]) {
      const response = await chatRoute.POST(
        request(JSON.stringify({ message: "hello", ...extra })),
      );
      expect(response.status).toBe(422);
    }
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("returns 415 for unsupported or missing content types", async () => {
    for (const contentType of ["text/plain", "application/x-www-form-urlencoded"]) {
      const response = await chatRoute.POST(request("{}", { contentType }));
      expect(response.status).toBe(415);
    }
    const missing = new Request("http://localhost/api/chat", {
      method: "POST",
      body: "{}",
    });
    expect((await chatRoute.POST(missing)).status).toBe(415);
  });

  it("returns controlled errors for missing and malformed JSON", async () => {
    const missing = await chatRoute.POST(request(""));
    const malformed = await chatRoute.POST(request("{not-json"));

    expect(missing.status).toBe(400);
    expect((await missing.json()).error.code).toBe("MISSING_REQUEST_BODY");
    expect(malformed.status).toBe(400);
    expect((await malformed.json()).error.code).toBe("INVALID_JSON");
  });

  it("enforces the streaming request-size limit even with a dishonest length", async () => {
    const response = await chatRoute.POST(
      request("x".repeat(CHAT_REQUEST_MAX_BYTES + 1), {
        headers: { "Content-Length": "1" },
      }),
    );
    expect(response.status).toBe(413);
  });

  it("rejects empty and over-limit messages with 422", async () => {
    for (const message of ["   ", "x".repeat(4001)]) {
      const response = await chatRoute.POST(
        request(JSON.stringify({ message })),
      );
      expect(response.status).toBe(422);
      expect((await response.json()).error.code).toBe("VALIDATION_ERROR");
    }
  });

  it.each([422, 429, 500, 502, 503, 504])(
    "normalizes a valid backend %s error",
    async (status) => {
      vi.spyOn(globalThis, "fetch").mockImplementation(async (_, init) => {
        const requestId =
          new Headers(init?.headers).get("X-Request-ID") ?? "missing";
        return upstreamJson(
          {
            error: {
              code: status === 429 ? "RATE_LIMITED" : "BACKEND_ERROR",
              message: "Safe backend message",
              request_id: requestId,
              retryable: status !== 422,
              field_errors:
                status === 422
                  ? [{ field: "body.message", message: "Invalid message" }]
                  : [],
            },
          },
          requestId,
          status,
          status === 429 ? { "Retry-After": "12" } : {},
        );
      });

      const response = await chatRoute.POST(
        request(JSON.stringify({ message: "hello" })),
      );
      const payload = await response.json();
      expect(response.status).toBe(status);
      expect(payload.error.requestId).toBe(
        response.headers.get("X-Request-ID"),
      );
      expect(JSON.stringify(payload)).not.toContain("Safe backend message");
      if (status === 422) {
        expect(payload.error.fieldErrors).toHaveLength(1);
      }
      if (status === 429) {
        expect(response.headers.get("Retry-After")).toBe("12");
      }
    },
  );

  it("maps backend 401 to service configuration failure, not user login", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (_, init) => {
      const requestId =
        new Headers(init?.headers).get("X-Request-ID") ?? "missing";
      return upstreamJson(
        {
          error: {
            code: "INVALID_API_KEY",
            message: "Invalid key",
            request_id: requestId,
          },
        },
        requestId,
        401,
      );
    });
    const response = await chatRoute.POST(
      request(JSON.stringify({ message: "hello" })),
    );
    const payload = await response.json();

    expect(response.status).toBe(503);
    expect(payload.error.code).toBe("UPSTREAM_CONFIGURATION_ERROR");
    expect(payload.error.message).not.toMatch(/log in|sign in|user/i);
  });

  it("returns safe configuration and network failures", async () => {
    delete process.env.BACKEND_API_KEY;
    const configuration = await chatRoute.POST(
      request(JSON.stringify({ message: "secret prompt" })),
    );
    expect(configuration.status).toBe(503);
    expect(await configuration.text()).not.toContain("secret prompt");

    process.env.BACKEND_API_KEY = backendKey;
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("secret-host"));
    const network = await chatRoute.POST(
      request(JSON.stringify({ message: "another prompt" })),
    );
    expect(network.status).toBe(503);
    expect(await network.text()).not.toContain("secret-host");
  });

  it("returns a distinct BFF timeout", async () => {
    vi.useFakeTimers();
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(
      (_, init) =>
        new Promise((_, reject) => {
          init?.signal?.addEventListener("abort", () =>
            reject(new DOMException("Aborted", "AbortError")),
          );
        }),
    );

    const timedOutPromise = chatRoute.POST(
      request(JSON.stringify({ message: "timeout" })),
    );
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchSpy).toHaveBeenCalledOnce();
    await vi.advanceTimersByTimeAsync(BFF_CHAT_TIMEOUT_MS);
    const timedOut = await timedOutPromise;
    expect(timedOut.status).toBe(504);
    expect((await timedOut.json()).error.code).toBe("BFF_TIMEOUT");
  });

  it("returns a distinct client cancellation where the runtime can respond", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(
      (_, init) =>
        new Promise((_, reject) => {
          init?.signal?.addEventListener("abort", () =>
            reject(new DOMException("Aborted", "AbortError")),
          );
        }),
    );
    const controller = new AbortController();
    const cancelledPromise = chatRoute.POST(
      request(JSON.stringify({ message: "cancel" }), {
        signal: controller.signal,
      }),
    );
    await vi.waitFor(() => expect(fetchSpy).toHaveBeenCalledOnce());
    controller.abort();
    const cancelled = await cancelledPromise;
    expect(cancelled.status).toBe(499);
    expect((await cancelled.json()).error.code).toBe("CLIENT_CANCELLED");
  });

  it.each([
    {
      name: "wrong content type",
      response: (requestId: string) =>
        new Response("{}", {
          headers: { "Content-Type": "text/plain", "X-Request-ID": requestId },
        }),
    },
    {
      name: "invalid JSON",
      response: (requestId: string) =>
        upstreamJson("{broken", requestId),
    },
    {
      name: "schema-invalid success",
      response: (requestId: string) =>
        upstreamJson({ assistant_message: "incomplete" }, requestId),
    },
    {
      name: "schema-invalid error",
      response: (requestId: string) =>
        upstreamJson({ detail: "internal" }, requestId, 500),
    },
    {
      name: "mismatched request ID",
      response: () => upstreamJson(validSuccess(), "different-request-id"),
    },
  ])("returns 502 for $name", async ({ response: makeResponse }) => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (_, init) =>
      makeResponse(
        new Headers(init?.headers).get("X-Request-ID") ?? "missing",
      ),
    );
    const response = await chatRoute.POST(
      request(JSON.stringify({ message: "hello" })),
    );
    expect(response.status).toBe(502);
    expect((await response.json()).error.code).toBe(
      "INVALID_UPSTREAM_RESPONSE",
    );
  });

  it("returns 502 for an oversized upstream response", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (_, init) => {
      const requestId =
        new Headers(init?.headers).get("X-Request-ID") ?? "missing";
      return upstreamJson(
        "x".repeat(CHAT_RESPONSE_MAX_BYTES + 1),
        requestId,
        200,
        { "Content-Length": String(CHAT_RESPONSE_MAX_BYTES + 1) },
      );
    });
    const response = await chatRoute.POST(
      request(JSON.stringify({ message: "hello" })),
    );
    expect(response.status).toBe(502);
  });

  it("never returns or logs secrets, prompts, raw responses, or backend URLs", async () => {
    const log = vi.spyOn(console, "log").mockImplementation(() => undefined);
    const error = vi.spyOn(console, "error").mockImplementation(() => undefined);
    mockSuccessfulBackend();
    const prompt = "private-prompt-value";
    const response = await chatRoute.POST(
      request(JSON.stringify({ message: prompt })),
    );
    const body = await response.text();

    for (const secret of [backendKey, backendUrl, prompt]) {
      expect(body).not.toContain(secret);
      expect(JSON.stringify(log.mock.calls)).not.toContain(secret);
      expect(JSON.stringify(error.mock.calls)).not.toContain(secret);
    }
  });

  it("exports POST without implementing another chat method", () => {
    expect(chatRoute.POST).toBeTypeOf("function");
    expect("GET" in chatRoute).toBe(false);
    expect("PUT" in chatRoute).toBe(false);
    expect("DELETE" in chatRoute).toBe(false);
  });
});
