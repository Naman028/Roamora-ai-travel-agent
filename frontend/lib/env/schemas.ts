import { z } from "zod";

export const clientEnvSchema = z.object({
  NEXT_PUBLIC_APP_ENV: z
    .enum(["development", "test", "production"])
    .default("development"),
});
