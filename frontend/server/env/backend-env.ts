import "server-only";

import { z } from "zod";

export const backendEnvInputSchema = z
  .object({
    BACKEND_API_URL: z
      .url()
      .refine((value) => {
        const url = new URL(value);
        return url.protocol === "http:" || url.protocol === "https:";
      }, "Backend URL must use HTTP or HTTPS."),
    BACKEND_API_KEY: z.string().min(1),
  })
  .strict();

export interface BackendEnv {
  baseUrl: string;
  apiKey: string;
}

export function readBackendEnv(): BackendEnv {
  const result = backendEnvInputSchema.safeParse({
    BACKEND_API_URL: process.env.BACKEND_API_URL,
    BACKEND_API_KEY: process.env.BACKEND_API_KEY,
  });

  if (!result.success) {
    throw new Error("Roamora backend configuration is unavailable.");
  }

  const url = new URL(result.data.BACKEND_API_URL);
  const isLocalhost =
    url.hostname === "localhost" || url.hostname === "127.0.0.1";
  if (
    process.env.NODE_ENV === "production" &&
    url.protocol !== "https:" &&
    !isLocalhost
  ) {
    throw new Error("Roamora backend transport configuration is unsafe.");
  }

  return {
    baseUrl: result.data.BACKEND_API_URL.replace(/\/+$/, ""),
    apiKey: result.data.BACKEND_API_KEY,
  };
}
